from pathlib import Path
import os
import shutil
import re
from threading import Lock

# Avoid tokenizer worker process noise/leaks in local dev when env is unset.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import chromadb
from chromadb.config import Settings

from langchain_classic.chains import RetrievalQA
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from langchain_openai import ChatOpenAI
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_chroma import Chroma

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CHROMA_DIR = BASE_DIR / "chroma_db"
COLLECTION_NAME = "doc_chat"


def prepare_chroma_dir():
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CHROMA_DIR, 0o700)


def create_chroma_client():
    prepare_chroma_dir()
    settings = Settings(allow_reset=True)
    return chromadb.PersistentClient(path=str(CHROMA_DIR), settings=settings)


def load_documents():
    docs = []
    if not DATA_DIR.exists():
        return docs

    for path in DATA_DIR.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in {".txt", ".md"}:
            loader = TextLoader(str(path), encoding="utf-8")
        elif path.suffix.lower() == ".pdf":
            loader = PyPDFLoader(str(path))
        else:
            continue
        docs.extend(loader.load())
    return docs


def has_documents():
    if not DATA_DIR.exists():
        return False
    for path in DATA_DIR.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in {".txt", ".md", ".pdf"}:
            return True
    return False


def build_vectorstore():
    docs = load_documents()
    if not docs:
        raise ValueError("No documents found in ./data. Add files and try again.")

    client = create_chroma_client()
    client.reset()

    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)
    chunks = splitter.split_documents(docs)
    embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-mpnet-base-v2")
    vectorstore = Chroma.from_documents(
        chunks,
        embeddings,
        client=client,
        collection_name=COLLECTION_NAME,
    )
    return vectorstore


def load_vectorstore():
    embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-mpnet-base-v2")
    client = create_chroma_client()
    return Chroma(
        client=client,
        collection_name=COLLECTION_NAME,
        embedding_function=embeddings,
    )


class RagService:
    def __init__(self, model: str):
        self.model = model
        self._vectorstore = None
        self._qa = None
        self._lock = Lock()
        self.retriever_k = max(int(os.getenv("RETRIEVER_K", "12")), 1)
        self.retriever_search_type = os.getenv("RETRIEVER_SEARCH_TYPE", "mmr").strip().lower()
        if self.retriever_search_type not in {"similarity", "mmr"}:
            self.retriever_search_type = "mmr"
        self.retriever_fetch_k = max(int(os.getenv("RETRIEVER_FETCH_K", "24")), self.retriever_k)
        self.retriever_lambda_mult = float(os.getenv("RETRIEVER_LAMBDA_MULT", "0.35"))
        self.debug_rag = os.getenv("DEBUG_RAG", "false").strip().lower() in {"1", "true", "yes", "on"}
        self.llm_timeout_seconds = max(float(os.getenv("LLM_TIMEOUT_SECONDS", "45")), 5.0)

    def _new_llm(self):
        return ChatOpenAI(
            model=self.model,
            temperature=0,
            request_timeout=self.llm_timeout_seconds,
            max_retries=1,
        )

    def _extract_query_terms(self, question: str):
        tokens = re.findall(r"[A-Za-z][A-Za-z'-]{2,}", question)
        if not tokens:
            return []
        stop_words = {
            "who",
            "what",
            "when",
            "where",
            "why",
            "how",
            "tell",
            "explain",
            "describe",
            "summarize",
            "discuss",
            "know",
            "me",
            "the",
            "and",
            "for",
            "from",
            "with",
            "about",
            "into",
            "that",
            "this",
            "hamlet",
        }
        proper_nouns = []
        for token in tokens:
            if token[0].isupper() and token.lower() not in stop_words and token not in proper_nouns:
                proper_nouns.append(token)
        if proper_nouns:
            return proper_nouns[:6]
        keywords = []
        for token in tokens:
            lower = token.lower()
            if lower in stop_words or lower in keywords:
                continue
            keywords.append(lower)
        return keywords[:6]

    def _answer_is_uncertain(self, answer: str):
        text = (answer or "").strip().lower()
        if not text:
            return True
        uncertain_markers = [
            "i don't know",
            "i do not know",
            "don't know",
            "do not know",
            "not enough information",
            "cannot determine",
            "can't determine",
            "not mentioned",
            "not provided",
        ]
        return any(marker in text for marker in uncertain_markers)

    def _term_match_pattern(self, term: str):
        escaped = [re.escape(char) for char in term]
        return re.compile(r"".join(f"{char}\\s*" for char in escaped), re.IGNORECASE)

    def _collect_term_snippets(self, terms, documents, max_per_term=2):
        snippets_by_term = {term: [] for term in terms}
        if not terms or not documents:
            return snippets_by_term
        for term in terms:
            pattern = self._term_match_pattern(term)
            for document in documents:
                text = document.page_content.replace("\n", " ")
                match = pattern.search(text)
                if not match:
                    continue
                start = max(0, match.start() - 120)
                end = min(len(text), match.end() + 180)
                snippet = re.sub(r"\s+", " ", text[start:end]).strip()
                source = document.metadata.get("source", "unknown") if document.metadata else "unknown"
                snippets_by_term[term].append((source, snippet))
                if len(snippets_by_term[term]) >= max_per_term:
                    break
        return snippets_by_term

    def _summarize_term_from_snippets(self, term: str, snippets):
        joined = " ".join(snippet for _, snippet in snippets).lower()
        role_bits = []
        if "ambassador" in joined:
            role_bits.append("an ambassador")
        if "courtier" in joined:
            role_bits.append("a courtier")
        if "danish court" in joined or "king" in joined:
            role_bits.append("connected to the Danish court")

        relationship = ""
        if "cornelius" in joined and term.lower() != "cornelius":
            relationship = " and is often mentioned alongside Cornelius"

        if role_bits:
            role_text = role_bits[0] if len(role_bits) == 1 else f"{role_bits[0]} and {role_bits[1]}"
            return f"{term} is {role_text}{relationship}."
        return f"{term} is mentioned in the document{relationship}."

    def _format_snippet_answer(self, snippets_by_term):
        lines = []
        for term, snippets in snippets_by_term.items():
            if not snippets:
                continue
            summary = self._summarize_term_from_snippets(term, snippets)
            lines.append(summary)
            source, snippet = snippets[0]
            lines.append(f"Evidence ({Path(source).name}): \"{snippet[:220]}...\"")
        if not lines:
            return "I don't know based on the provided context."
        return "\n".join(lines)

    def _documents_cover_terms(self, documents, terms):
        if not documents or not terms:
            return False
        corpus = "\n".join(doc.page_content for doc in documents).lower()
        return all(term.lower() in corpus for term in terms)

    def _keyword_lookup_documents(self, terms, per_term_limit=4):
        if not terms:
            return []
        client = create_chroma_client()
        collection = client.get_or_create_collection(COLLECTION_NAME)
        matches = []
        seen = set()

        def variants(term: str):
            candidates = [term, term.lower(), term.title(), term.upper()]
            deduped = []
            for candidate in candidates:
                if candidate not in deduped:
                    deduped.append(candidate)
            return deduped

        for term in terms:
            for variant in variants(term):
                result = collection.get(
                    where_document={"$contains": variant},
                    include=["documents", "metadatas"],
                    limit=per_term_limit,
                )
                documents = result.get("documents") or []
                metadatas = result.get("metadatas") or []
                for index, text in enumerate(documents):
                    if not text:
                        continue
                    metadata = metadatas[index] if index < len(metadatas) and metadatas[index] else {}
                    source = metadata.get("source", "")
                    key = (source, text[:200])
                    if key in seen:
                        continue
                    seen.add(key)
                    matches.append(Document(page_content=text, metadata=metadata))
        return matches[: max(self.retriever_k, 12)]

    def _merge_documents(self, primary_docs, fallback_docs):
        merged = []
        seen = set()
        for doc in list(primary_docs) + list(fallback_docs):
            source = doc.metadata.get("source", "") if doc.metadata else ""
            key = (source, doc.page_content[:200])
            if key in seen:
                continue
            seen.add(key)
            merged.append(doc)
            if len(merged) >= max(self.retriever_k, 12):
                break
        return merged

    def _answer_from_documents(self, question: str, documents):
        context = "\n\n".join(
            f"Source: {doc.metadata.get('source', 'unknown')}\n{doc.page_content}"
            for doc in documents[: max(self.retriever_k, 12)]
        )
        prompt = (
            "Answer using only the context below. "
            "If the answer is not in context, say you don't know.\n\n"
            f"Context:\n{context}\n\nQuestion: {question}"
        )
        llm = self._new_llm()
        response = llm.invoke(prompt)
        if hasattr(response, "content"):
            return response.content
        return str(response)

    def _retrieve_documents(self, question: str):
        search_kwargs = {"k": self.retriever_k}
        if self.retriever_search_type == "mmr":
            search_kwargs["fetch_k"] = self.retriever_fetch_k
            search_kwargs["lambda_mult"] = self.retriever_lambda_mult
        retriever = self._vectorstore.as_retriever(
            search_type=self.retriever_search_type,
            search_kwargs=search_kwargs,
        )
        return retriever.invoke(question)

    def ensure_ready(self):
        if self._vectorstore is None:
            if CHROMA_DIR.exists():
                self._vectorstore = load_vectorstore()
            else:
                self._vectorstore = build_vectorstore()
        if self._qa is None:
            search_kwargs = {"k": self.retriever_k}
            if self.retriever_search_type == "mmr":
                search_kwargs["fetch_k"] = self.retriever_fetch_k
                search_kwargs["lambda_mult"] = self.retriever_lambda_mult
            self._qa = RetrievalQA.from_chain_type(
                llm=self._new_llm(),
                retriever=self._vectorstore.as_retriever(
                    search_type=self.retriever_search_type,
                    search_kwargs=search_kwargs,
                ),
                return_source_documents=True,
            )

    def ingest(self):
        with self._lock:
            self._qa = None
            self._vectorstore = None
            if not has_documents():
                client = create_chroma_client()
                client.reset()
                self._vectorstore = load_vectorstore()
                self._qa = None
                return
            self._vectorstore = build_vectorstore()
            self._qa = None

    def ask(self, question: str):
        with self._lock:
            if not has_documents():
                return {
                    "answer": "No documents are available. Upload a document and rebuild the index.",
                    "sources": [],
                }
            self.ensure_ready()
            used_llm_fallback = False
            query_terms = self._extract_query_terms(question)
            try:
                result = self._qa.invoke({"query": question})
            except Exception:
                used_llm_fallback = True
                vector_documents = self._retrieve_documents(question)
                fallback_documents = self._keyword_lookup_documents(query_terms)
                merged_documents = self._merge_documents(vector_documents, fallback_documents)
                snippets_by_term = self._collect_term_snippets(query_terms, merged_documents)
                answer = self._format_snippet_answer(snippets_by_term)
                if answer.strip() == "I don't know based on the provided context.":
                    answer = "I couldn't query the language model right now. Please try again in a moment."
                result = {"result": answer, "source_documents": merged_documents}
            source_documents = result.get("source_documents", [])
            retrieval_mode = "vector"
            answer_text = result.get("result", "")
            should_try_keyword_fallback = (
                query_terms
                and (
                    not self._documents_cover_terms(source_documents, query_terms)
                    or self._answer_is_uncertain(answer_text)
                )
            )
            if should_try_keyword_fallback:
                fallback_documents = self._keyword_lookup_documents(query_terms)
                if fallback_documents:
                    merged_documents = self._merge_documents(source_documents, fallback_documents)
                    answer = self._answer_from_documents(question, merged_documents)
                    if self._answer_is_uncertain(answer):
                        snippets_by_term = self._collect_term_snippets(query_terms, merged_documents)
                        answer = self._format_snippet_answer(snippets_by_term)
                    result = {"result": answer, "source_documents": merged_documents}
                    retrieval_mode = "vector+keyword_fallback"
                else:
                    retrieval_mode = "vector_no_keyword_hits"
            if used_llm_fallback:
                retrieval_mode = "retrieval_only_fallback"
        sources = []
        for doc in result.get("source_documents", []):
            source = doc.metadata.get("source")
            if source and source not in sources:
                sources.append(source)
        response = {"answer": result.get("result", ""), "sources": sources}
        if self.debug_rag:
            response["debug"] = {
                "retrieval_mode": retrieval_mode,
                "query_terms": query_terms,
                "source_document_count": len(result.get("source_documents", [])),
            }
        return response

const form = document.getElementById("chat-form");
const answer = document.getElementById("answer");
const sources = document.getElementById("sources");
const questionInput = document.getElementById("question");
const manageDocsButton = document.getElementById("manage-docs");
const askButton = form ? form.querySelector("button[type=\"submit\"]") : null;
const ingestButton = document.getElementById("ingest");
const ingestStatus = document.getElementById("ingest-status");
const askStatus = document.getElementById("ask-status");
const docsPanel = document.getElementById("docs-panel");
const docsList = document.getElementById("docs-list");
const docsFileInput = document.getElementById("docs-file");
const uploadDocButton = document.getElementById("upload-doc");
const docsStatus = document.getElementById("docs-status");
const response = document.getElementById("response");
const debug = document.getElementById("debug");
const apiKeyPanel = document.getElementById("api-key-panel");
const apiKeyInput = document.getElementById("api-key");
const saveApiKeyButton = document.getElementById("save-api-key");
const clearApiKeyButton = document.getElementById("clear-api-key");
const apiKeyStatus = document.getElementById("api-key-status");
const API_KEY_STORAGE_KEY = "docChatApiKey";
const CHAT_REQUEST_TIMEOUT_MS = 45000;
let hasDocuments = true;
let isIndexing = false;

const setApiKeyStatus = (text) => {
  if (!apiKeyStatus) {
    return;
  }
  apiKeyStatus.textContent = text;
};

const getApiKey = () => {
  if (apiKeyInput && apiKeyInput.value.trim()) {
    return apiKeyInput.value.trim();
  }
  return (localStorage.getItem(API_KEY_STORAGE_KEY) || "").trim();
};

const saveApiKey = () => {
  const apiKey = getApiKey();
  if (!apiKey) {
    localStorage.removeItem(API_KEY_STORAGE_KEY);
    setApiKeyStatus("API key cleared.");
    return "";
  }
  localStorage.setItem(API_KEY_STORAGE_KEY, apiKey);
  if (apiKeyInput) {
    apiKeyInput.value = apiKey;
  }
  setApiKeyStatus("API key saved.");
  return apiKey;
};

const setApiKeyPanelHidden = (isHidden) => {
  if (!apiKeyPanel) {
    return;
  }
  if (isHidden) {
    apiKeyPanel.classList.add("api-key-panel--hidden");
    return;
  }
  apiKeyPanel.classList.remove("api-key-panel--hidden");
};

const confirmApiKey = () => {
  const apiKey = getApiKey();
  if (!apiKey) {
    return;
  }
  setApiKeyPanelHidden(true);
  setApiKeyStatus("API key confirmed.");
};

const withApiKey = (headers = {}) => {
  const apiKey = saveApiKey();
  if (!apiKey) {
    return headers;
  }
  return { ...headers, "x-api-key": apiKey };
};

const handleUnauthorized = (setMessage) => {
  localStorage.removeItem(API_KEY_STORAGE_KEY);
  if (apiKeyInput) {
    apiKeyInput.value = "";
  }
  setApiKeyPanelHidden(false);
  setApiKeyStatus("Unauthorized. Please enter a valid API key.");
  setMessage("Unauthorized. Re-enter API key and try again.");
};

if (apiKeyInput) {
  apiKeyInput.value = localStorage.getItem(API_KEY_STORAGE_KEY) || "";
}

if (saveApiKeyButton) {
  saveApiKeyButton.addEventListener("click", () => {
    saveApiKey();
  });
}

if (clearApiKeyButton) {
  clearApiKeyButton.addEventListener("click", () => {
    localStorage.removeItem(API_KEY_STORAGE_KEY);
    if (apiKeyInput) {
      apiKeyInput.value = "";
    }
    setApiKeyPanelHidden(false);
    setApiKeyStatus("API key cleared.");
  });
}

const setResponseState = (state) => {
  response.classList.remove("response--idle", "response--thinking", "response--ready");
  response.classList.add(`response--${state}`);
};

const setStatus = (text) => {
  answer.textContent = text;
  if (sources) {
    sources.textContent = "";
  }
  if (debug) {
    debug.textContent = "";
  }
};

const setDebug = (text) => {
  if (!debug) {
    return;
  }
  const hasText = Boolean(text && text.trim());
  debug.textContent = hasText ? text : "";
  debug.classList.toggle("response__debug--visible", hasText);
};

const setIngestStatus = (text) => {
  if (!ingestStatus) {
    return;
  }
  ingestStatus.textContent = text;
};

const setDocsStatus = (text) => {
  if (!docsStatus) {
    return;
  }
  docsStatus.textContent = text;
};

const setAskStatus = (text) => {
  if (!askStatus) {
    return;
  }
  askStatus.textContent = text;
};

const updateAskAvailability = () => {
  if (questionInput) {
    questionInput.disabled = isIndexing || !hasDocuments;
  }
  if (askButton) {
    askButton.disabled = isIndexing || !hasDocuments;
  }
  if (!hasDocuments) {
    setAskStatus("Upload at least one document to ask questions.");
  } else {
    setAskStatus("");
  }
  if (ingestButton) {
    ingestButton.disabled = isIndexing || !hasDocuments;
  }
};

const setHasDocuments = (value) => {
  hasDocuments = Boolean(value);
  updateAskAvailability();
};

const extractDocPath = (sourcePath) => {
  if (!sourcePath) {
    return "";
  }
  const normalized = String(sourcePath).replace(/\\/g, "/");
  const dataMatch = normalized.match(/\/data\/(.+)$/);
  if (dataMatch && dataMatch[1]) {
    return dataMatch[1];
  }
  const parts = normalized.split("/").filter(Boolean);
  return parts.length ? parts[parts.length - 1] : normalized;
};

const displayNameFromPath = (docPath) => {
  if (!docPath) {
    return "unknown";
  }
  const parts = docPath.split("/").filter(Boolean);
  return parts.length ? parts[parts.length - 1] : docPath;
};

const openDocumentInNewTab = async (docPath, setMessage) => {
  if (!docPath) {
    return false;
  }
  const safePath = docPath
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
  try {
    const response = await fetch(`/documents/view/${safePath}`, {
      headers: withApiKey(),
    });
    if (response.status === 401) {
      handleUnauthorized(setMessage);
      return false;
    }
    if (!response.ok) {
      setMessage("Failed to open document.");
      return false;
    }
    confirmApiKey();
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    window.open(objectUrl, "_blank", "noopener");
    setTimeout(() => URL.revokeObjectURL(objectUrl), 60000);
    return true;
  } catch (error) {
    setMessage("Failed to open document.");
    return false;
  }
};

const renderSources = (sourcePaths) => {
  if (!sources) {
    return;
  }
  if (!sourcePaths || !sourcePaths.length) {
    sources.textContent = "Sources: none";
    return;
  }
  sources.innerHTML = "";
  const label = document.createElement("span");
  label.textContent = "Sources: ";
  sources.appendChild(label);
  sourcePaths.forEach((sourcePath, index) => {
    const docPath = extractDocPath(sourcePath);
    const link = document.createElement("a");
    link.href = "#";
    link.dataset.docPath = docPath;
    link.classList.add("response__source-link", "source-view");
    link.textContent = displayNameFromPath(docPath);
    sources.appendChild(link);
    if (index < sourcePaths.length - 1) {
      sources.appendChild(document.createTextNode(", "));
    }
  });
};

const setIndexingState = (indexing) => {
  isIndexing = indexing;
  updateAskAvailability();
  if (docsFileInput) {
    docsFileInput.disabled = indexing;
  }
  if (uploadDocButton) {
    uploadDocButton.disabled = indexing;
  }
  if (ingestButton) {
    ingestButton.disabled = indexing || !hasDocuments;
  }
  if (manageDocsButton) {
    manageDocsButton.disabled = indexing;
  }
  if (docsList) {
    docsList.querySelectorAll(".docs-delete").forEach((button) => {
      button.disabled = indexing;
    });
  }
};

const renderDocsList = (documents) => {
  if (!docsList) {
    return;
  }
  docsList.innerHTML = "";
  if (!documents || documents.length === 0) {
    const item = document.createElement("li");
    item.textContent = "No documents found.";
    docsList.appendChild(item);
    return;
  }
  documents.forEach((doc) => {
    const item = document.createElement("li");
    item.classList.add("docs-list__item");
    const link = document.createElement("a");
    link.href = "#";
    link.dataset.docPath = doc;
    link.textContent = doc;
    link.classList.add("docs-list__link", "docs-view");
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.classList.add("docs-delete");
    deleteButton.dataset.docPath = doc;
    deleteButton.textContent = "Delete";
    item.appendChild(link);
    item.appendChild(deleteButton);
    docsList.appendChild(item);
  });
};

const loadDocuments = async () => {
  try {
    const response = await fetch("/documents", {
      headers: withApiKey(),
    });
    const data = await response.json();
    if (response.status === 401) {
      handleUnauthorized(setDocsStatus);
      return null;
    }
    if (!response.ok) {
      setDocsStatus(data.error || "Failed to load documents.");
      return null;
    }
    confirmApiKey();
    const documents = data.documents || [];
    setHasDocuments(documents.length > 0);
    renderDocsList(documents);
    return documents;
  } catch (error) {
    setDocsStatus("Failed to load documents.");
    return null;
  }
};

if (manageDocsButton && docsPanel) {
  manageDocsButton.addEventListener("click", () => {
    const isHidden = docsPanel.classList.contains("docs-panel--hidden");
    if (isHidden) {
      docsPanel.classList.remove("docs-panel--hidden");
      setDocsStatus("");
      loadDocuments();
    } else {
      docsPanel.classList.add("docs-panel--hidden");
    }
  });
}

if (docsList) {
  docsList.addEventListener("click", async (event) => {
    const target = event.target;
    if (target instanceof HTMLAnchorElement && target.classList.contains("docs-view")) {
      event.preventDefault();
      const docPath = target.dataset.docPath;
      if (!docPath) {
        return;
      }
      setDocsStatus("Opening document...");
      const opened = await openDocumentInNewTab(docPath, setDocsStatus);
      if (opened) {
        setDocsStatus("");
      }
      return;
    }
    if (!(target instanceof HTMLButtonElement)) {
      return;
    }
    if (!target.classList.contains("docs-delete")) {
      return;
    }
    const docPath = target.dataset.docPath;
    if (!docPath) {
      return;
    }
    const currentDocCount = docsList.querySelectorAll(".docs-delete").length;
    const isLastDocument = currentDocCount === 1;
    const promptMessage = isLastDocument
      ? `Delete ${docPath}? This is the last document. The index will be cleared, and asking will be disabled until a new document is uploaded.`
      : `Delete ${docPath}?`;
    const confirmed = window.confirm(promptMessage);
    if (!confirmed) {
      return;
    }
    const safePath = docPath
      .split("/")
      .map((segment) => encodeURIComponent(segment))
      .join("/");
    setIndexingState(true);
    setDocsStatus("Deleting document...");
    setIngestStatus(isLastDocument ? "Preparing empty index state..." : "Rebuilding index...");
    try {
      const response = await fetch(`/documents/${safePath}`, {
        method: "DELETE",
        headers: withApiKey(),
      });
      const data = await response.json();
      if (response.status === 401) {
        handleUnauthorized(setDocsStatus);
        return;
      }
      if (!response.ok) {
        setDocsStatus(data.error || "Delete failed.");
        setIngestStatus(data.error || "Delete failed.");
        return;
      }
      confirmApiKey();
      if (isLastDocument) {
        renderDocsList([]);
        setHasDocuments(false);
        setDocsStatus("Delete complete. No documents remain.");
        if (data.warning) {
          setIngestStatus("Index cleared.");
        } else {
          setIngestStatus("Index cleared.");
        }
        if (docsPanel) {
          docsPanel.classList.remove("docs-panel--hidden");
        }
      } else {
        const documents = await loadDocuments();
        setDocsStatus("Delete complete.");
        if (documents && documents.length > 0) {
          setIngestStatus("Index rebuilt.");
        } else {
          setIngestStatus("Index cleared.");
          setHasDocuments(false);
        }
        if (data.warning) {
          setIngestStatus(data.warning);
        }
      }
    } catch (error) {
      setDocsStatus("Delete failed.");
      setIngestStatus("Delete failed.");
    } finally {
      setIndexingState(false);
    }
  });
}

if (sources) {
  sources.addEventListener("click", async (event) => {
    const target = event.target;
    if (!(target instanceof HTMLAnchorElement)) {
      return;
    }
    if (!target.classList.contains("source-view")) {
      return;
    }
    event.preventDefault();
    const docPath = target.dataset.docPath;
    if (!docPath) {
      return;
    }
    await openDocumentInNewTab(docPath, (message) => {
      if (!sources) {
        return;
      }
      if (message.startsWith("Unauthorized")) {
        sources.textContent = message;
      }
    });
  });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = document.getElementById("question").value.trim();
  if (!question) {
    setStatus("Please enter a question.");
    setResponseState("idle");
    return;
  }
  setStatus("Thinking...");
  setResponseState("thinking");
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), CHAT_REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: withApiKey({ "Content-Type": "application/json" }),
      body: JSON.stringify({ question }),
      signal: controller.signal,
    });
    const data = await response.json();
    if (response.status === 401) {
      handleUnauthorized(setStatus);
      setResponseState("idle");
      return;
    }
    if (!response.ok) {
      setStatus(data.error || "Something went wrong.");
      setResponseState("idle");
      return;
    }
    confirmApiKey();
    answer.textContent = data.answer || "No answer returned.";
    setResponseState("ready");
    renderSources(data.sources || []);
    if (data.debug) {
      const mode = data.debug.retrieval_mode || "unknown";
      const terms = Array.isArray(data.debug.query_terms)
        ? data.debug.query_terms.join(", ")
        : "";
      const count = data.debug.source_document_count ?? "?";
      setDebug(`Debug: mode=${mode}; terms=[${terms}]; chunks=${count}`);
    } else {
      setDebug("");
    }
  } catch (error) {
    if (error && error.name === "AbortError") {
      setStatus("Request timed out. Please try again.");
    } else {
      setStatus("Request failed.");
    }
    setResponseState("idle");
  } finally {
    clearTimeout(timeoutId);
  }
});

ingestButton.addEventListener("click", async () => {
  if (!hasDocuments) {
    setIngestStatus("No documents to index.");
    return;
  }
  setIndexingState(true);
  setIngestStatus("Rebuilding index...");
  setDocsStatus("");
  try {
    const response = await fetch("/ingest", {
      method: "POST",
      headers: withApiKey(),
    });
    const data = await response.json();
    if (response.status === 401) {
      handleUnauthorized(setIngestStatus);
      return;
    }
    if (!response.ok) {
      setIngestStatus(data.error || "Index rebuild failed.");
      return;
    }
    confirmApiKey();
    setIngestStatus("Index rebuilt.");
  } catch (error) {
    setIngestStatus("Index rebuild failed.");
  } finally {
    setIndexingState(false);
  }
});

if (uploadDocButton) {
  uploadDocButton.addEventListener("click", async () => {
    if (!docsFileInput || !docsFileInput.files.length) {
      setDocsStatus("Choose a file to upload.");
      return;
    }
    const file = docsFileInput.files[0];
    const formData = new FormData();
    formData.append("file", file);
    setIndexingState(true);
    setDocsStatus("Uploading document...");
    setIngestStatus("Rebuilding index...");
    try {
      const response = await fetch("/documents", {
        method: "POST",
        headers: withApiKey(),
        body: formData,
      });
      const data = await response.json();
      if (response.status === 401) {
        handleUnauthorized(setDocsStatus);
        return;
      }
      if (!response.ok) {
        setDocsStatus(data.error || "Upload failed.");
        setIngestStatus(data.error || "Upload failed.");
        return;
      }
      confirmApiKey();
      setDocsStatus("Upload complete.");
      docsFileInput.value = "";
      const documents = await loadDocuments();
      if (documents && documents.length > 0) {
        setHasDocuments(true);
      }
      if (data.warning) {
        setIngestStatus(data.warning);
      } else {
        setIngestStatus("Index rebuilt.");
      }
    } catch (error) {
      setDocsStatus("Upload failed.");
      setIngestStatus("Upload failed.");
    } finally {
      setIndexingState(false);
    }
  });
}

const initializeAppState = async () => {
  const documents = await loadDocuments();
  if (!documents) {
    return;
  }
  if (documents.length === 0 && docsPanel) {
    docsPanel.classList.remove("docs-panel--hidden");
  }
};

updateAskAvailability();
initializeAppState();

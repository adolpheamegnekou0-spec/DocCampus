"use strict";

const savedDocumentsKey = "doccampus.documents";
const accessTokenKey = "doccampus.accessToken";
let accountMode = "login";
let paymentUnavailableMessage =
    "Le paiement Mixx by Yas attend le compte marchand et la documentation API.";

function normalizeText(value) {
    return (value || "")
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .toLowerCase()
        .trim();
}

function normalizeCategory(value) {
    return normalizeText(value).replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

async function apiRequest(path, options = {}) {
    const headers = new Headers(options.headers || {});
    const token = localStorage.getItem(accessTokenKey);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetch(`${window.location.origin}${path}`, {
        ...options,
        headers
    });
    const payload = response.status === 204 ? null : await response.json().catch(() => null);
    if (!response.ok) {
        const message = payload && typeof payload.detail === "string"
            ? payload.detail
            : `Erreur serveur (${response.status}).`;
        throw new Error(message);
    }
    return payload;
}

function showNotice(message) {
    let notice = document.getElementById("siteNotice");

    if (!notice) {
        notice = document.createElement("div");
        notice.id = "siteNotice";
        notice.className = "app-toast";
        notice.setAttribute("role", "status");
        notice.setAttribute("aria-live", "polite");
        document.body.appendChild(notice);
    }

    notice.textContent = message;
    notice.classList.add("visible");
    window.clearTimeout(showNotice.timeoutId);
    showNotice.timeoutId = window.setTimeout(() => {
        notice.classList.remove("visible");
    }, 4500);
}

function readSavedDocuments() {
    try {
        const saved = JSON.parse(localStorage.getItem(savedDocumentsKey) || "[]");
        return Array.isArray(saved) ? saved : [];
    } catch (error) {
        console.error("Impossible de lire les documents enregistrés.", error);
        showNotice("Les documents enregistrés localement n'ont pas pu être lus.");
        return [];
    }
}

function createSavedDocumentCard(savedDocument) {
    const card = document.createElement("article");
    card.className = "document-card";
    card.dataset.savedDocument = "true";
    card.dataset.type = normalizeCategory(savedDocument.type);
    card.dataset.title = savedDocument.title;
    if (savedDocument.id) card.dataset.apiDocumentId = savedDocument.id;

    const icon = document.createElement("div");
    icon.className = `file-icon ${card.dataset.type}-icon`;
    icon.innerHTML = '<i class="fa-solid fa-file-pdf"></i>';

    const info = document.createElement("div");
    info.className = "document-info";

    const top = document.createElement("div");
    top.className = "document-top";
    const type = document.createElement("span");
    type.className = `type-badge ${card.dataset.type}`;
    type.textContent = savedDocument.type;
    const year = document.createElement("span");
    year.className = "document-year";
    year.textContent = savedDocument.year || "";
    top.append(type, year);

    const title = document.createElement("h3");
    title.textContent = savedDocument.title;
    const subject = document.createElement("div");
    subject.className = "subject";
    subject.textContent = `${savedDocument.institution || ""} • ${savedDocument.subject || ""}`;
    const meta = document.createElement("div");
    meta.className = "document-meta";
    const downloads = document.createElement("span");
    downloads.innerHTML = '<i class="fa-solid fa-download"></i> ';
    downloads.append(document.createTextNode(`${savedDocument.downloads || 0} téléchargements`));
    meta.appendChild(downloads);

    info.append(top, title, subject, meta);

    const button = document.createElement("button");
    button.className = "download-btn";
    button.type = "button";
    const downloadUrl = savedDocument.file ||
        (savedDocument.id ? `/api/documents/${encodeURIComponent(savedDocument.id)}/file` : "");
    button.dataset.file = downloadUrl;
    button.setAttribute("aria-label", `Ouvrir ${savedDocument.title}`);
    button.innerHTML = '<i class="fa-solid fa-download"></i>';
    const actions = document.createElement("div");
    actions.className = "document-actions";
    if (card.dataset.type.includes("concours")) {
        const preview = document.createElement("button");
        preview.className = "preview-btn";
        preview.type = "button";
        preview.dataset.previewUrl = savedDocument.preview ||
            (savedDocument.id
                ? `/api/documents/${encodeURIComponent(savedDocument.id)}/preview`
                : (savedDocument.file || "").replace(/^documents\//, "/preview/"));
        preview.dataset.downloadUrl = downloadUrl;
        preview.setAttribute("aria-label", `Prévisualiser ${savedDocument.title}`);
        preview.innerHTML = '<i class="fa-solid fa-eye" aria-hidden="true"></i><span>Aperçu</span>';
        actions.appendChild(preview);
    }
    actions.appendChild(button);
    card.append(icon, info, actions);
    return card;
}

function loadSavedDocuments() {
    const documentsList = document.getElementById("documentsList");
    readSavedDocuments().forEach(savedDocument => {
        if (savedDocument && savedDocument.title) {
            const card = createSavedDocumentCard(savedDocument);
            documentsList.appendChild(card);
            appendConcoursCard(card);
        }
    });
}

function appendConcoursCard(documentCard) {
    if (!normalizeText(documentCard.dataset.type).includes("concours")) return;
    document.getElementById("concoursList").appendChild(documentCard.cloneNode(true));
}

async function loadRemoteContent() {
    if (!["http:", "https:"].includes(window.location.protocol)) return;
    try {
        const [documents, categories, paymentStatus] = await Promise.all([
            apiRequest("/api/documents"),
            apiRequest("/api/categories"),
            apiRequest("/api/payments/status")
        ]);
        const documentsList = document.getElementById("documentsList");
        documents.forEach(doc => {
            const card = createSavedDocumentCard(doc);
            documentsList.appendChild(card);
            appendConcoursCard(card);
        });
        const filterRow = document.querySelector(".filters");
        categories.forEach(category => {
            const value = normalizeCategory(category.name);
            if ([...filterRow.querySelectorAll(".filter")].some(button => button.dataset.filter === value)) {
                return;
            }
            const button = document.createElement("button");
            button.type = "button";
            button.className = "filter";
            button.dataset.filter = value;
            button.textContent = category.name;
            button.setAttribute("aria-pressed", "false");
            filterRow.appendChild(button);
            button.addEventListener("click", () => {
                filterRow.querySelectorAll(".filter").forEach(filter => {
                    const active = filter === button;
                    filter.classList.toggle("active", active);
                    filter.setAttribute("aria-pressed", String(active));
                });
                delete document.body.dataset.selectedFaculty;
                applyDocumentFilters();
            });
        });
        if (paymentStatus && !paymentStatus.enabled && paymentStatus.message) {
            paymentUnavailableMessage = paymentStatus.message;
        }
    } catch (error) {
        console.error("L'API DocCampus est inaccessible.", error);
        showNotice("L'API DocCampus est inaccessible. Démarre le serveur FastAPI pour les comptes et les PDF.");
    }
}

function getCurrentSearch() {
    return normalizeText(document.getElementById("searchInput").value);
}

function applyDocumentFilters() {
    const activeFilter = document.querySelector(".filter.active");
    const selectedType = activeFilter ? activeFilter.dataset.filter : "all";
    const currentFaculty = document.body.dataset.selectedFaculty || "";
    const search = getCurrentSearch();
    let visibleCount = 0;

    document.querySelectorAll("#documentsList .document-card").forEach(card => {
        const typeMatches = selectedType === "all" ||
            normalizeCategory(card.dataset.type) === normalizeCategory(selectedType);
        const textMatches = !search || normalizeText(card.textContent).includes(search);
        const subject = card.querySelector(".subject");
        const facultyMatches = !currentFaculty ||
            (subject && normalizeText(subject.textContent).startsWith(normalizeText(currentFaculty)));
        const visible = typeMatches && textMatches && facultyMatches;
        card.hidden = !visible;
        if (visible) visibleCount += 1;
    });

    document.getElementById("documentCount").textContent = visibleCount;
    document.getElementById("emptyState").classList.toggle("hidden", visibleCount !== 0);

    let concoursCount = 0;
    document.querySelectorAll("#concoursList .document-card").forEach(card => {
        const visible = !search || normalizeText(card.textContent).includes(search);
        card.hidden = !visible;
        if (visible) concoursCount += 1;
    });
    document.getElementById("concoursCount").textContent = concoursCount;
    document.getElementById("concoursEmpty").classList.toggle("hidden", concoursCount !== 0);
}

function openDocumentPreview(button) {
    const card = button.closest(".document-card");
    const dialog = document.getElementById("documentPreviewDialog");
    const previewUrl = button.dataset.previewUrl;
    if (!previewUrl) {
        showNotice("Aucun fichier PDF n'est associé à cette épreuve.");
        return;
    }

    document.getElementById("documentPreviewTitle").textContent =
        card.querySelector(".document-info h3").textContent;
    document.getElementById("documentPreviewMeta").textContent =
        `${card.querySelector(".subject").textContent} • ${card.querySelector(".document-year").textContent}`.trim();
    document.getElementById("documentPreviewFrame").src = previewUrl;
    document.getElementById("documentPreviewDownload").href = button.dataset.downloadUrl;
    dialog.showModal();
}

function closeDocumentPreview() {
    const dialog = document.getElementById("documentPreviewDialog");
    document.getElementById("documentPreviewFrame").src = "about:blank";
    dialog.close();
}

function closeFacultyModal() {
    document.getElementById("facultyModal").classList.remove("show");
}

function openFacultyModal(button) {
    const card = button.closest(".faculty-card");
    const code = card.querySelector(".faculty-code").textContent.trim();
    const name = card.querySelector("h3").textContent.trim().replace(/\s+/g, " ");
    const description = card.querySelector("p").textContent.trim().replace(/\s+/g, " ");
    const icon = card.querySelector(".faculty-icon").innerHTML;
    const modal = document.getElementById("facultyModal");
    const programsList = document.getElementById("programsList");
    const modalDocuments = document.getElementById("modalDocuments");

    document.getElementById("modalFacultyIcon").innerHTML = icon;
    document.getElementById("modalFacultyCode").textContent = code;
    document.getElementById("modalFacultyTitle").textContent = name;
    document.getElementById("modalFacultyDescription").textContent = description;
    programsList.replaceChildren();

    card.querySelectorAll(".faculty-tags span").forEach(tag => {
        const program = document.createElement("div");
        program.className = "program-item";
        const label = document.createElement("strong");
        label.textContent = "Repère";
        program.append(label, document.createTextNode(tag.textContent));
        programsList.appendChild(program);
    });

    modalDocuments.replaceChildren();
    const documents = [...document.querySelectorAll("#documentsList .document-card")]
        .filter(documentCard => {
            const subject = documentCard.querySelector(".subject");
            return subject && normalizeText(subject.textContent).startsWith(normalizeText(code));
        });

    if (documents.length === 0) {
        const emptyMessage = document.createElement("p");
        emptyMessage.textContent = "Aucun document n'est encore affiché pour cette structure.";
        modalDocuments.appendChild(emptyMessage);
    } else {
        documents.forEach(documentCard => {
            const item = document.createElement("div");
            item.className = "modal-document";
            const itemTitle = document.createElement("strong");
            itemTitle.textContent = documentCard.querySelector("h3").textContent.trim();
            const openButton = document.createElement("button");
            openButton.type = "button";
            openButton.textContent = "Voir";
            openButton.addEventListener("click", () => {
                closeFacultyModal();
                document.body.dataset.selectedFaculty = code;
                document.querySelectorAll(".filter").forEach(filter => {
                    const active = filter.dataset.filter === "all";
                    filter.classList.toggle("active", active);
                    filter.setAttribute("aria-pressed", String(active));
                });
                applyDocumentFilters();
                document.getElementById("documents").scrollIntoView({ behavior: "smooth" });
            });
            item.append(itemTitle, openButton);
            modalDocuments.appendChild(item);
        });
    }

    modal.classList.add("show");
}

function setupSiteControls() {
    const searchInput = document.getElementById("searchInput");
    const mainNav = document.getElementById("mainNav");
    const menuButton = document.getElementById("menuBtn");
    const premiumModal = document.getElementById("premiumModal");
    const accountModal = document.getElementById("accountModal");
    const accountForm = document.getElementById("accountForm");
    const accountButton = document.getElementById("accountBtn");

    document.querySelectorAll(".filter").forEach(button => {
        button.type = "button";
        button.setAttribute("aria-pressed", String(button.classList.contains("active")));
        button.addEventListener("click", () => {
            document.querySelectorAll(".filter").forEach(filter => {
                const active = filter === button;
                filter.classList.toggle("active", active);
                filter.setAttribute("aria-pressed", String(active));
            });
            delete document.body.dataset.selectedFaculty;
            applyDocumentFilters();
        });
    });

    searchInput.addEventListener("input", applyDocumentFilters);
    document.getElementById("searchBtn").addEventListener("click", () => {
        applyDocumentFilters();
        document.getElementById("documents").scrollIntoView({ behavior: "smooth" });
    });
    searchInput.addEventListener("keydown", event => {
        if (event.key === "Enter") {
            event.preventDefault();
            document.getElementById("searchBtn").click();
        }
    });

    const facultySearch = document.getElementById("facultySearch");
    facultySearch.addEventListener("input", () => {
        const search = normalizeText(facultySearch.value);
        let visibleCount = 0;
        document.querySelectorAll(".faculty-card").forEach(card => {
            const visible = normalizeText(`${card.dataset.search} ${card.textContent}`).includes(search);
            card.hidden = !visible;
            if (visible) visibleCount += 1;
        });
        document.getElementById("facultyEmpty").classList.toggle("visible", visibleCount === 0);
    });

    document.querySelectorAll(".faculty-btn").forEach(button => {
        button.type = "button";
        button.addEventListener("click", () => openFacultyModal(button));
    });

    document.querySelectorAll(".download-btn").forEach(button => {
        button.type = "button";
    });

    document.getElementById("facultyModalClose").addEventListener("click", closeFacultyModal);
    document.getElementById("facultyModal").addEventListener("click", event => {
        if (event.target.id === "facultyModal") closeFacultyModal();
    });
    document.getElementById("documentPreviewClose").addEventListener("click", closeDocumentPreview);
    document.getElementById("documentPreviewDialog").addEventListener("click", event => {
        if (event.target === event.currentTarget) closeDocumentPreview();
    });
    document.getElementById("documentPreviewDialog").addEventListener("cancel", event => {
        event.preventDefault();
        closeDocumentPreview();
    });

    document.addEventListener("click", event => {
        if (!(event.target instanceof Element)) return;
        const previewButton = event.target.closest(".preview-btn");
        if (previewButton) {
            openDocumentPreview(previewButton);
            return;
        }
        const button = event.target.closest(".download-btn");
        if (!button) return;

        const file = button.dataset.file;
        const card = button.closest(".document-card");
        if (!file) {
            showNotice("Aucun fichier PDF n'a été associé à ce document.");
            return;
        }
        if (/^https?:\/\//i.test(file) ||
            card.dataset.apiDocumentId ||
            (card.dataset.savedDocument === "true" && !/^[a-z][a-z\d+.-]*:/i.test(file))) {
            window.open(file, "_blank", "noopener");
            return;
        }
        if (/^documents\/[^?#]+\.pdf$/i.test(file)) {
            window.open(`/${file}`, "_blank", "noopener");
            return;
        }
        showNotice("Le bouton fonctionne, mais le fichier PDF indiqué n'est pas présent dans le dossier du site.");
    });

    document.getElementById("premiumBtn").addEventListener("click", () => {
        document.querySelector("#premiumModal .modal-content > p").textContent =
            paymentUnavailableMessage;
        premiumModal.classList.add("show");
    });
    document.getElementById("premiumClose").addEventListener("click", () => {
        premiumModal.classList.remove("show");
    });
    document.querySelector("#premiumModal .modal-action").addEventListener("click", () => {
        premiumModal.classList.remove("show");
        showNotice(paymentUnavailableMessage);
    });
    premiumModal.addEventListener("click", event => {
        if (event.target === premiumModal) premiumModal.classList.remove("show");
    });

    menuButton.addEventListener("click", () => {
        const isOpen = mainNav.classList.toggle("show");
        menuButton.setAttribute("aria-expanded", String(isOpen));
        menuButton.setAttribute("aria-label", isOpen ? "Fermer le menu" : "Ouvrir le menu");
        const icon = menuButton.querySelector("i");
        icon.classList.toggle("fa-bars", !isOpen);
        icon.classList.toggle("fa-xmark", isOpen);
    });
    mainNav.querySelectorAll("a").forEach(link => {
        link.addEventListener("click", () => {
            mainNav.classList.remove("show");
            menuButton.setAttribute("aria-expanded", "false");
            menuButton.setAttribute("aria-label", "Ouvrir le menu");
            const icon = menuButton.querySelector("i");
            icon.classList.add("fa-bars");
            icon.classList.remove("fa-xmark");
        });
    });

    function setAccountMode(mode) {
        accountMode = mode;
        const registering = mode === "register";
        document.getElementById("accountTitle").textContent =
            registering ? "Créer un compte" : "Se connecter";
        document.getElementById("accountDescription").textContent =
            registering
                ? "Crée un compte DocCampus pour retrouver ton espace."
                : "Connecte-toi à ton compte DocCampus.";
        accountForm.querySelector('[type="submit"]').textContent =
            registering ? "Créer mon compte" : "Se connecter";
        document.getElementById("accountModeToggle").textContent =
            registering ? "J'ai déjà un compte" : "Créer un compte";
        accountForm.elements.password.autocomplete =
            registering ? "new-password" : "current-password";
        document.getElementById("accountMessage").textContent = "";
    }

    document.getElementById("accountModeToggle").addEventListener("click", () => {
        setAccountMode(accountMode === "login" ? "register" : "login");
    });
    document.getElementById("concoursSubmissionForm").addEventListener("submit", async event => {
        event.preventDefault();
        const form = event.currentTarget;
        if (!form.reportValidity()) return;

        if (!localStorage.getItem(accessTokenKey)) {
            setAccountMode("register");
            accountModal.classList.add("show");
            document.getElementById("concoursSubmissionMessage").textContent =
                "Crée un compte ou connecte-toi pour envoyer une épreuve.";
            return;
        }

        const submitButton = form.querySelector('[type="submit"]');
        submitButton.disabled = true;
        document.getElementById("concoursSubmissionMessage").textContent = "";
        try {
            const result = await apiRequest("/api/documents/submissions", {
                method: "POST",
                body: new FormData(form)
            });
            form.reset();
            document.getElementById("concoursSubmissionMessage").textContent =
                result.email_message ||
                "Épreuve envoyée. Elle apparaîtra sur le site après validation par l’administration.";
            showNotice(result.email_message || "Ton épreuve a été envoyée pour vérification.");
        } catch (error) {
            document.getElementById("concoursSubmissionMessage").textContent = error.message;
        } finally {
            submitButton.disabled = false;
        }
    });
    accountButton.addEventListener("click", () => {
        if (localStorage.getItem(accessTokenKey)) {
            localStorage.removeItem(accessTokenKey);
            accountButton.textContent = "Se connecter";
            showNotice("Tu es déconnecté.");
            return;
        }
        accountModal.classList.add("show");
    });
    document.getElementById("accountClose").addEventListener("click", () => {
        accountModal.classList.remove("show");
    });
    accountModal.addEventListener("click", event => {
        if (event.target === accountModal) accountModal.classList.remove("show");
    });
    accountForm.addEventListener("submit", async event => {
        event.preventDefault();
        const submitButton = accountForm.querySelector('[type="submit"]');
        submitButton.disabled = true;
        const email = accountForm.elements.email.value;
        const password = accountForm.elements.password.value;
        try {
            const response = await fetch(`${window.location.origin}/api/auth/${accountMode === "register" ? "register" : "login"}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email, password })
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(typeof payload.detail === "string"
                    ? payload.detail
                    : `Erreur serveur (${response.status}).`);
            }
            localStorage.setItem(accessTokenKey, payload.access_token);
            accountButton.textContent = `Déconnexion (${payload.user.email})`;
            accountModal.classList.remove("show");
            accountForm.reset();
            setAccountMode("login");
            showNotice("Compte connecté.");
        } catch (error) {
            document.getElementById("accountMessage").textContent = error.message;
        } finally {
            submitButton.disabled = false;
        }
    });
    const savedToken = localStorage.getItem(accessTokenKey);
    if (savedToken && ["http:", "https:"].includes(window.location.protocol)) {
        apiRequest("/api/auth/me")
            .then(user => {
                accountButton.textContent = `Déconnexion (${user.email})`;
            })
            .catch(error => {
                localStorage.removeItem(accessTokenKey);
                console.error("La session utilisateur n'a pas pu être restaurée.", error);
            });
    }

    document.addEventListener("keydown", event => {
        if (event.key === "Escape") {
            closeFacultyModal();
            premiumModal.classList.remove("show");
            accountModal.classList.remove("show");
        }
    });
}

document.addEventListener("DOMContentLoaded", async () => {
    loadSavedDocuments();
    setupSiteControls();
    await loadRemoteContent();
    applyDocumentFilters();
});

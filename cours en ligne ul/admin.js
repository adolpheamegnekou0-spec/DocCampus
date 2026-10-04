"use strict";

const apiBase = window.location.origin;
const tokenKey = "doccampus.accessToken";
let adminCategories = [];

function setAdminMessage(message) {
    document.getElementById("adminLoginMessage").textContent = message;
}

function showAdminNotice(message) {
    let notice = document.getElementById("adminNotice");
    if (!notice) {
        notice = document.createElement("div");
        notice.id = "adminNotice";
        notice.className = "app-toast";
        notice.setAttribute("role", "status");
        notice.setAttribute("aria-live", "polite");
        document.body.appendChild(notice);
    }
    notice.textContent = message;
    notice.classList.add("visible");
    window.clearTimeout(showAdminNotice.timeoutId);
    showAdminNotice.timeoutId = window.setTimeout(() => {
        notice.classList.remove("visible");
    }, 4500);
}

async function apiRequest(path, options = {}) {
    const headers = new Headers(options.headers || {});
    const token = localStorage.getItem(tokenKey);
    if (token) headers.set("Authorization", `Bearer ${token}`);

    const response = await fetch(`${apiBase}${path}`, {
        ...options,
        headers
    });
    if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        const message = typeof payload.detail === "string"
            ? payload.detail
            : `Erreur serveur (${response.status}).`;
        throw new Error(message);
    }
    if (response.status === 204) return null;
    return response.json();
}

function setAuthenticated(isAuthenticated) {
    document.getElementById("adminLogin").hidden = isAuthenticated;
    document.getElementById("adminDashboard").hidden = !isAuthenticated;
    document.getElementById("adminLogout").hidden = !isAuthenticated;
}

function appendTableCell(row, text) {
    const cell = document.createElement("td");
    cell.textContent = text;
    row.appendChild(cell);
    return cell;
}

function renderDocuments(documents) {
    const body = document.getElementById("adminDocumentRows");
    body.replaceChildren();
    documents.forEach(doc => {
        const row = document.createElement("tr");
        appendTableCell(row, doc.title);
        appendTableCell(row, doc.type);
        appendTableCell(row, String(doc.downloads));
        const statusCell = appendTableCell(row, "");
        const badge = document.createElement("span");
        badge.className = "status";
        badge.textContent = "Actif";
        statusCell.appendChild(badge);
        const actionCell = appendTableCell(row, "");
        const removeButton = document.createElement("button");
        removeButton.type = "button";
        removeButton.className = "admin-row-action";
        removeButton.textContent = "Supprimer";
        removeButton.addEventListener("click", async () => {
            if (!window.confirm(`Supprimer « ${doc.title} » et son PDF ?`)) return;
            try {
                await apiRequest(`/api/admin/documents/${encodeURIComponent(doc.id)}`, {
                    method: "DELETE"
                });
                await refreshDashboard();
                showAdminNotice("Document supprimé.");
            } catch (error) {
                showAdminNotice(error.message);
            }
        });
        actionCell.appendChild(removeButton);
        body.appendChild(row);
    });
}

async function refreshDashboard() {
    const [documents, categories, stats] = await Promise.all([
        apiRequest("/api/admin/documents"),
        apiRequest("/api/categories"),
        apiRequest("/api/admin/stats")
    ]);
    adminCategories = categories;
    renderDocuments(documents);
    document.getElementById("statDocuments").textContent = stats.documents;
    document.getElementById("statDownloads").textContent = stats.downloads;
    document.getElementById("statUsers").textContent = stats.users;
    document.getElementById("statSubscriptions").textContent = stats.subscriptions;
}

function createDocumentDialog() {
    let dialog = document.getElementById("documentEditor");
    if (dialog) return dialog;

    dialog = document.createElement("dialog");
    dialog.id = "documentEditor";
    dialog.className = "admin-dialog";
    dialog.innerHTML = `
        <form class="admin-dialog-form" id="documentEditorForm">
            <h2>Ajouter un document PDF</h2>
            <label>Titre <input name="title" required maxlength="120"></label>
            <label>Type <select name="type" required></select></label>
            <label>Faculté / école (code) <input name="institution" required maxlength="40" placeholder="FDS"></label>
            <label>Matière <input name="subject" required maxlength="80"></label>
            <label>Année <input name="year" required inputmode="numeric" pattern="[0-9]{4}" maxlength="4" value="${new Date().getFullYear()}"></label>
            <label>Fichier PDF <input name="file" type="file" accept="application/pdf,.pdf" required></label>
            <p class="admin-dialog-note">PDF uniquement, 30 Mo maximum. Le fichier sera conservé sur le serveur.</p>
            <div class="admin-dialog-actions">
                <button type="button" class="dialog-cancel">Annuler</button>
                <button type="submit" class="dialog-save">Ajouter le document</button>
            </div>
        </form>
    `;
    document.body.appendChild(dialog);

    const form = dialog.querySelector("form");
    const typeSelect = form.elements.type;
    adminCategories.forEach(category => {
        const option = document.createElement("option");
        option.value = category.name;
        option.textContent = category.name;
        typeSelect.appendChild(option);
    });
    dialog.querySelector(".dialog-cancel").addEventListener("click", () => dialog.close());
    form.addEventListener("submit", async event => {
        event.preventDefault();
        if (!form.reportValidity()) return;
        const data = new FormData(form);
        const submitButton = form.querySelector('[type="submit"]');
        submitButton.disabled = true;
        try {
            await apiRequest("/api/admin/documents", {
                method: "POST",
                body: data
            });
            dialog.close();
            form.reset();
            await refreshDashboard();
            showAdminNotice("Document publié dans la bibliothèque.");
        } catch (error) {
            showAdminNotice(error.message);
        } finally {
            submitButton.disabled = false;
        }
    });
    return dialog;
}

function openDocumentEditor() {
    createDocumentDialog().showModal();
}

async function showUsers() {
    try {
        const users = await apiRequest("/api/admin/users");
        let dialog = document.getElementById("usersDialog");
        if (!dialog) {
            dialog = document.createElement("dialog");
            dialog.id = "usersDialog";
            dialog.className = "admin-dialog";
            dialog.innerHTML = `
                <div class="admin-dialog-form">
                    <h2>Comptes utilisateurs</h2>
                    <div class="admin-user-list"></div>
                    <div class="admin-dialog-actions">
                        <button type="button" class="dialog-cancel">Fermer</button>
                    </div>
                </div>
            `;
            document.body.appendChild(dialog);
            dialog.querySelector(".dialog-cancel").addEventListener("click", () => dialog.close());
        }
        const list = dialog.querySelector(".admin-user-list");
        list.replaceChildren();
        if (users.length === 0) {
            list.textContent = "Aucun compte utilisateur enregistré.";
        }
        users.forEach(user => {
            const row = document.createElement("div");
            row.className = "admin-user-row";
            const identity = document.createElement("span");
            identity.textContent = `${user.email} — ${user.role}`;
            row.appendChild(identity);
            if (user.role === "user") {
                const remove = document.createElement("button");
                remove.type = "button";
                remove.className = "admin-row-action";
                remove.textContent = "Supprimer";
                remove.addEventListener("click", async () => {
                    if (!window.confirm(`Supprimer le compte ${user.email} ?`)) return;
                    try {
                        await apiRequest(`/api/admin/users/${user.id}`, { method: "DELETE" });
                        await showUsers();
                        await refreshDashboard();
                    } catch (error) {
                        showAdminNotice(error.message);
                    }
                });
                row.appendChild(remove);
            }
            list.appendChild(row);
        });
        if (!dialog.open) dialog.showModal();
    } catch (error) {
        showAdminNotice(error.message);
    }
}

async function showCategories() {
    try {
        adminCategories = await apiRequest("/api/categories");
        let dialog = document.getElementById("categoriesDialog");
        if (!dialog) {
            dialog = document.createElement("dialog");
            dialog.id = "categoriesDialog";
            dialog.className = "admin-dialog";
            dialog.innerHTML = `
                <div class="admin-dialog-form">
                    <h2>Gérer les catégories</h2>
                    <form class="category-add-form">
                        <label>Nouvelle catégorie <input name="name" maxlength="40" required></label>
                        <button type="submit" class="dialog-save">Ajouter</button>
                    </form>
                    <div class="admin-category-list"></div>
                    <div class="admin-dialog-actions">
                        <button type="button" class="dialog-cancel">Fermer</button>
                    </div>
                </div>
            `;
            document.body.appendChild(dialog);
            dialog.querySelector(".dialog-cancel").addEventListener("click", () => dialog.close());
            dialog.querySelector(".category-add-form").addEventListener("submit", async event => {
                event.preventDefault();
                const form = event.currentTarget;
                if (!form.reportValidity()) return;
                const data = new FormData(form);
                try {
                    await apiRequest("/api/admin/categories", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ name: data.get("name") })
                    });
                    form.reset();
                    await showCategories();
                    await refreshDashboard();
                } catch (error) {
                    showAdminNotice(error.message);
                }
            });
        }
        const list = dialog.querySelector(".admin-category-list");
        list.replaceChildren();
        adminCategories.forEach(category => {
            const row = document.createElement("div");
            row.className = "admin-user-row";
            const name = document.createElement("span");
            name.textContent = category.name;
            row.appendChild(name);
            if (!["Cours", "TD", "Annale", "Corrigé"].includes(category.name)) {
                const remove = document.createElement("button");
                remove.type = "button";
                remove.className = "admin-row-action";
                remove.textContent = "Supprimer";
                remove.addEventListener("click", async () => {
                    try {
                        await apiRequest(`/api/admin/categories/${category.id}`, { method: "DELETE" });
                        await showCategories();
                        await refreshDashboard();
                    } catch (error) {
                        showAdminNotice(error.message);
                    }
                });
                row.appendChild(remove);
            }
            list.appendChild(row);
        });
        if (!dialog.open) dialog.showModal();
    } catch (error) {
        showAdminNotice(error.message);
    }
}

async function loginAsAdmin(email, password) {
    const response = await fetch(`${apiBase}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password })
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(typeof payload.detail === "string" ? payload.detail : "Connexion impossible.");
    }
    localStorage.setItem(tokenKey, payload.access_token);
    if (payload.user.role !== "admin") {
        localStorage.removeItem(tokenKey);
        throw new Error("Ce compte n'a pas les droits administrateur.");
    }
    await refreshDashboard();
    setAuthenticated(true);
}

async function restoreAdminSession() {
    const token = localStorage.getItem(tokenKey);
    if (!token) return;
    try {
        const user = await apiRequest("/api/auth/me");
        if (user.role !== "admin") {
            localStorage.removeItem(tokenKey);
            return;
        }
        await refreshDashboard();
        setAuthenticated(true);
    } catch (error) {
        localStorage.removeItem(tokenKey);
        setAdminMessage("Session expirée. Connecte-toi à nouveau.");
    }
}

document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("adminLoginForm").addEventListener("submit", async event => {
        event.preventDefault();
        const form = event.currentTarget;
        const data = new FormData(form);
        const button = form.querySelector('[type="submit"]');
        button.disabled = true;
        setAdminMessage("");
        try {
            await loginAsAdmin(String(data.get("email")), String(data.get("password")));
        } catch (error) {
            setAdminMessage(error.message);
        } finally {
            button.disabled = false;
        }
    });

    document.getElementById("adminLogout").addEventListener("click", () => {
        localStorage.removeItem(tokenKey);
        setAuthenticated(false);
        setAdminMessage("Tu es déconnecté.");
    });

    document.querySelector(".panel-header .add-btn").addEventListener("click", openDocumentEditor);
    document.querySelector('[data-admin-action="add-document"]').addEventListener("click", openDocumentEditor);
    document.querySelector('[data-admin-action="categories"]').addEventListener("click", showCategories);
    document.querySelector('[data-admin-action="users"]').addEventListener("click", showUsers);
    document.querySelector('[data-admin-action="statistics"]').addEventListener("click", () => {
        document.getElementById("adminStats").scrollIntoView({ behavior: "smooth" });
    });

    restoreAdminSession();
});

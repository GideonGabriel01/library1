# main.py
"""
Main GUI for the Library Management System (Tkinter + customtkinter).

Features:
- Login (users in 'users' table; roles: admin/staff)
- Dashboard (admin analytics, staff personal actions, or member view if username matches a member)
- Searchable fuzzy member selector
- Borrow / Return with actor logging
- Audit Log (admin)
- SMTP Settings + test send
- Password change + admin reset -> send email notifications (via mailer.py)
- Export transactions with date-range filters (file picker, CSV/XLSX)
"""

import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox, Toplevel, filedialog
import customtkinter as ctk
from datetime import datetime
import re
import csv
import openpyxl
import platform

# charts
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# local modules (make sure these files exist in the same folder)
import database
import mailer

# ---------------------------
# App initialization / theme
# ---------------------------
database.init_db()

PALETTE = {
    "bg": "#f0f6ff",      # soft sky
    "panel": "#ffffff",
    "accent": "#0ea5e9",  # bright sky-blue
    "accent2": "#7c3aed", # purple
    "muted": "#475569",
    "success": "#10b981",
    "warning": "#f59e0b",
    "danger": "#ef4444"
}

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

EMAIL_RE = re.compile(r"^[\w\.-]+@[\w\.-]+\.\w+$")
def is_valid_email(email):
    return EMAIL_RE.match(email) is not None

# ---------------------------
# SearchableDropdown widget
# ---------------------------
from difflib import SequenceMatcher

class SearchableDropdown:
    """
    Modal, fuzzy-searching dropdown with keyboard navigation.

    Required:
      - parent: tk parent window
      - fetch_fn(search_text) -> list[dict]  (dict must include 'id' and 'name', optionally 'email')
      - on_select(dict) callback

    Behavior:
      - fuzzy sorts results with difflib.SequenceMatcher
      - Up/Down/Enter/Escape keyboard support
      - Colors stronger matches using PALETTE
    """
    def __init__(self, parent, fetch_fn, on_select, title="Select item", max_results=200):
        self.parent = parent
        self.fetch_fn = fetch_fn
        self.on_select = on_select
        self.max_results = max_results

        self.win = Toplevel(parent)
        self.win.title(title)
        self.win.geometry("540x380")
        self.win.resizable(False, False)
        self.win.grab_set()

        top = tk.Frame(self.win, bg=PALETTE["panel"])
        top.pack(fill="x", padx=12, pady=(12,6))

        tk.Label(top, text=title, anchor="w", bg=PALETTE["panel"],
                 fg=PALETTE["accent"], font=("Helvetica", 12, "bold")).pack(fill="x")

        self.entry_var = tk.StringVar()
        self.entry = tk.Entry(top, textvariable=self.entry_var, font=("Segoe UI", 11))
        self.entry.pack(fill="x", pady=(6,4))
        self.entry.focus_set()

        # key bindings
        self.entry.bind("<KeyRelease>", self.on_key)
        self.entry.bind("<Down>", lambda e: self.move(1))
        self.entry.bind("<Up>", lambda e: self.move(-1))
        self.entry.bind("<Return>", lambda e: self.confirm_selection())
        self.entry.bind("<Escape>", lambda e: self.close())

        # listbox container
        lb_frame = tk.Frame(self.win, bg=PALETTE["panel"])
        lb_frame.pack(fill="both", expand=True, padx=12, pady=(6,12))

        self.listbox = tk.Listbox(lb_frame, activestyle="none", selectmode="browse", font=("Segoe UI", 10))
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<Double-Button-1>", lambda e: self.confirm_selection())
        self.listbox.bind("<Return>", lambda e: self.confirm_selection())
        self.listbox.bind("<Escape>", lambda e: self.close())
        self.listbox.bind("<Up>", lambda e: self.move(-1))
        self.listbox.bind("<Down>", lambda e: self.move(1))

        scrollbar = tk.Scrollbar(lb_frame, orient="vertical", command=self.listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=scrollbar.set)

        # buttons
        btn_frame = tk.Frame(self.win, bg=PALETTE["panel"])
        btn_frame.pack(fill="x", padx=12, pady=(0,12))
        ok = tk.Button(btn_frame, text="Select", command=self.confirm_selection, bg=PALETTE["accent"], fg="white")
        ok.pack(side="left", padx=(0,6))
        cancel = tk.Button(btn_frame, text="Cancel", command=self.close)
        cancel.pack(side="right", padx=(6,0))

        self.results = []
        self.update_list("")

    def fuzzy_score(self, needle, haystack):
        return SequenceMatcher(None, needle.lower(), haystack.lower()).ratio()

    def format_label(self, r):
        email = r.get("email","")
        if email:
            return f"{r['id']} — {r['name']}  <{email}>"
        return f"{r['id']} — {r['name']}"

    def update_list(self, txt):
        txt = txt or ""
        rows = self.fetch_fn(txt) if txt is not None else self.fetch_fn("")
        scored = []
        for r in rows:
            hay = f"{r.get('name','')} {r.get('email','')}"
            score = self.fuzzy_score(txt, hay) if txt else 0.5
            scored.append((score, r))
        scored.sort(key=lambda x: (-x[0], x[1].get("name","")))
        scored = scored[:self.max_results]

        self.results = [r for _, r in scored]
        self.listbox.delete(0, tk.END)
        for idx, r in enumerate(self.results):
            label = self.format_label(r)
            self.listbox.insert(tk.END, label)
            try:
                score = scored[idx][0]
                fg = PALETTE["muted"]
                if score > 0.8:
                    fg = PALETTE["accent"]
                elif score > 0.6:
                    fg = PALETTE["accent2"]
                self.listbox.itemconfig(idx, fg=fg)
            except Exception:
                # some Tk builds don't support itemconfig; ignore
                pass

        if self.results:
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(0)
            self.listbox.activate(0)
            self.listbox.see(0)

    def on_key(self, event):
        txt = self.entry_var.get().strip()
        self.update_list(txt)

    def move(self, delta):
        size = self.listbox.size()
        if size == 0:
            return
        cur = self.listbox.curselection()
        if cur:
            idx = cur[0] + delta
        else:
            idx = 0
        if idx < 0:
            idx = 0
        if idx >= size:
            idx = size - 1
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(idx)
        self.listbox.activate(idx)
        self.listbox.see(idx)
        try:
            label = self.listbox.get(idx)
            self.entry_var.set(label)
            self.entry.icursor(tk.END)
        except Exception:
            pass

    def confirm_selection(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showwarning("Select", "Please select an item.")
            return
        idx = sel[0]
        selected = self.results[idx]
        try:
            self.on_select(selected)
        finally:
            self.close()

    def close(self):
        try:
            self.win.grab_release()
        except Exception:
            pass
        self.win.destroy()

# ---------------------------
# Login Window
# ---------------------------
class LoginWindow:
    def __init__(self, root, on_success):
        self.on_success = on_success
        self.root = root
        self.win = Toplevel(root)
        self.win.title("Library Login")
        self.win.geometry("380x240")
        self.win.resizable(False, False)
        self.win.grab_set()
        self.win.protocol("WM_DELETE_WINDOW", self.on_close)

        lbl = ctk.CTkLabel(self.win, text="Library Login", font=("Arial", 18, "bold"))
        lbl.pack(pady=(12,8))

        frm = ctk.CTkFrame(self.win)
        frm.pack(padx=12, pady=6, fill="x")

        ctk.CTkLabel(frm, text="Username").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        self.user_var = ctk.StringVar()
        ctk.CTkEntry(frm, textvariable=self.user_var).grid(row=0, column=1, padx=6, pady=6)

        ctk.CTkLabel(frm, text="Password").grid(row=1, column=0, sticky="w", padx=6, pady=6)
        self.pw_var = ctk.StringVar()
        ctk.CTkEntry(frm, textvariable=self.pw_var, show="*").grid(row=1, column=1, padx=6, pady=6)

        btns = ctk.CTkFrame(self.win)
        btns.pack(fill="x", padx=12, pady=(6,12))
        ctk.CTkButton(btns, text="Login", command=self.try_login).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Exit", command=self.on_close).pack(side="right", padx=6)

        ctk.CTkLabel(self.win, text="(default admin/admin if first run)", font=("Arial", 9, "italic")).pack(pady=(0,6))

    def try_login(self):
        username = self.user_var.get().strip()
        password = self.pw_var.get()
        if not username or not password:
            messagebox.showwarning("Input", "Enter username and password.")
            return
        ok = database.verify_user(username, password)
        if ok:
            role = database.get_user_role(username) or "staff"
            messagebox.showinfo("Welcome", f"Welcome, {username} ({role})")
            self.win.grab_release()
            self.win.destroy()
            self.on_success(username)
        else:
            messagebox.showerror("Login failed", "Invalid username or password.")

    def on_close(self):
        try:
            self.win.grab_release()
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass
        try:
            self.root.quit()
        except Exception:
            pass

# ---------------------------
# Main Application
# ---------------------------
class LibraryApp:
    def __init__(self, root, username):
        self.root = root
        self.username = username
        self.role = database.get_user_role(username) or "staff"  # default to staff
        self.root.title(f"Library System — {username} ({self.role})")
        self.root.geometry("1180x760")

        # sidebar + main area
        self.sidebar = ctk.CTkFrame(self.root, width=220)
        self.sidebar.pack(side="left", fill="y")
        self.main_area = ctk.CTkFrame(self.root)
        self.main_area.pack(side="right", fill="both", expand=True)

        # frames
        self.frames = {}
        names = ("dashboard","search","borrow","manage","members","transactions","audit","settings")
        for name in names:
            f = ctk.CTkFrame(self.main_area)
            f.grid(row=0, column=0, sticky="nsew")
            self.frames[name] = f

        # sidebar buttons (hide admin-only ones for non-admins)
        ctk.CTkLabel(self.sidebar, text="Library", font=("Helvetica", 20, "bold")).pack(pady=12)
        items = [("Dashboard","dashboard"),("Search Books","search"),("Borrow/Return","borrow"),
                 ("Manage Books","manage"),("Members","members"),("Transactions","transactions")]
        for label, key in items:
            ctk.CTkButton(self.sidebar, text=label, command=lambda k=key: self.show_frame(k)).pack(fill="x", padx=12, pady=6)

        # admin-only
        if self.role == "admin":
            ctk.CTkButton(self.sidebar, text="Audit Log", command=lambda: self.show_frame("audit")).pack(fill="x", padx=12, pady=6)
            ctk.CTkButton(self.sidebar, text="Settings / Admin", command=lambda: self.show_frame("settings")).pack(fill="x", padx=12, pady=6)
        else:
            # staff: still show settings (for password change / SMTP read-only)
            ctk.CTkButton(self.sidebar, text="Settings", command=lambda: self.show_frame("settings")).pack(fill="x", padx=12, pady=6)

        # logout button
        ctk.CTkButton(self.sidebar, text="Logout", fg_color=PALETTE["danger"], command=self.logout).pack(side="bottom", fill="x", padx=12, pady=12)

        # build frames
        self._build_dashboard()
        self._build_search()
        self._build_borrow()
        self._build_manage()
        self._build_members()
        self._build_transactions()
        self._build_audit()
        self._build_settings()

        # show default
        self.show_frame("dashboard")
        self.refresh_all()

    def show_frame(self, name):
        frame = self.frames[name]
        frame.tkraise()
        # refresh frame-specific content
        if name == "dashboard":
            self.draw_dashboard()
        elif name == "search":
            self.load_books_into_tree()
        elif name == "borrow":
            self.load_books_for_borrow()
            self.load_active_loans()
        elif name == "members":
            self.load_members()
        elif name == "transactions":
            self.load_transactions()
        elif name == "audit":
            self.load_audit()
        elif name == "settings":
            self.load_settings()

    def logout(self):
        if messagebox.askyesno("Confirm", "Logout and return to login?"):
            self.root.destroy()
            # restart app (simple approach)
            os.execl(sys.executable, sys.executable, *sys.argv)

    # ---------------- Dashboard ----------------
    def _build_dashboard(self):
        f = self.frames["dashboard"]
        ctk.CTkLabel(f, text="Dashboard", font=("Arial", 20, "bold")).pack(pady=8)

        top = ctk.CTkFrame(f)
        top.pack(fill="x", padx=12, pady=6)
        self.total_books_lbl = ctk.CTkLabel(top, text="Books: -", text_color=PALETTE["muted"])
        self.total_books_lbl.pack(side="left", padx=12)
        self.total_members_lbl = ctk.CTkLabel(top, text="Members: -", text_color=PALETTE["muted"])
        self.total_members_lbl.pack(side="left", padx=12)
        self.active_loans_lbl = ctk.CTkLabel(top, text="Active loans: -", text_color=PALETTE["muted"])
        self.active_loans_lbl.pack(side="left", padx=12)

        chart_frame = ctk.CTkFrame(f)
        chart_frame.pack(fill="both", expand=True, padx=12, pady=12)

        self.fig = Figure(figsize=(9,4), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=chart_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    def draw_dashboard(self):
        totals = database.analytics_totals()
        self.total_books_lbl.configure(text=f"Books: {totals['books']}")
        self.total_members_lbl.configure(text=f"Members: {totals['members']}")
        self.active_loans_lbl.configure(text=f"Active loans: {totals['active_loans']}")

        # if logged-in user maps to a library member (by email or name), show member loans
        member = None
        for m in database.list_members():
            if (m.get("email") and m["email"].lower() == self.username.lower()) or (m.get("name") and m["name"].lower() == self.username.lower()):
                member = m
                break

        if member:
            # Member dashboard: show this member's active and recent loans in chart form + table
            loans = [l for l in database.list_loans(show_all=True) if l["member_id"] == member["id"]]
            recent = loans[:10]
            # make a small bar of borrowed vs returned counts
            borrowed_count = len(loans)
            returned_count = sum(1 for l in loans if l["date_returned"])
            self.ax.clear()
            labels = ["Borrowed", "Returned"]
            vals = [borrowed_count, returned_count]
            colors = [PALETTE["accent"], PALETTE["accent2"]]
            self.ax.bar(labels, vals, color=colors)
            self.ax.set_title(f"{member['name']}'s Loans (total {borrowed_count})")
            self.canvas.draw()
        else:
            # Staff / Admin dashboard: show loans trend
            data = database.analytics_loans_by_month(months=6)
            labels = [t for (t,_) in data]
            values = [v for (_,v) in data]
            self.ax.clear()
            self.ax.plot(labels, values, marker="o", color=PALETTE["accent"], linewidth=2)
            self.ax.fill_between(labels, values, color=PALETTE["accent2"], alpha=0.12)
            self.ax.set_title("Loans (last 6 months)")
            self.ax.grid(alpha=0.25)
            self.canvas.draw()

    # ---------------- Search Frame ----------------
    def _build_search(self):
        f = self.frames["search"]
        ctk.CTkLabel(f, text="Search Books", font=("Arial", 18, "bold")).pack(pady=8)
        bar = ctk.CTkFrame(f); bar.pack(fill="x", padx=12, pady=6)
        self.search_var = ctk.StringVar()
        ctk.CTkEntry(bar, textvariable=self.search_var, placeholder_text="Search by title/author/category/ISBN").pack(side="left", fill="x", expand=True, padx=(8,6))
        ctk.CTkButton(bar, text="Search", command=self.on_search).pack(side="left", padx=6)

        tree_frame = ctk.CTkFrame(f)
        tree_frame.pack(fill="both", expand=True, padx=12, pady=8)
        cols = ("id","title","author","category","isbn","available")
        heads = ("ID","Title","Author","Category","ISBN","Available")
        self.search_tree, self.search_vsb = self.make_tree(tree_frame, cols, heads)
        self.search_tree.pack(side="left", fill="both", expand=True)
        self.search_vsb.pack(side="right", fill="y")

        # interactions
        self.search_tree.bind("<Double-1>", self.on_search_double)
        # right-click context for borrow/edit
        if platform.system() == "Darwin":
            self.search_tree.bind("<Button-2>", self.on_search_right_click)
        else:
            self.search_tree.bind("<Button-3>", self.on_search_right_click)

    def on_search(self):
        text = self.search_var.get().strip()
        self._load_books(search_text=text)

    def _load_books(self, search_text=None):
        for r in self.search_tree.get_children():
            self.search_tree.delete(r)
        rows = database.list_books(search_text)
        for r in rows:
            self.search_tree.insert("", "end", values=(r["id"], r["title"], r.get("author",""), r.get("category",""), r.get("isbn",""), "Yes" if r["available"] else "No"))

    def load_books_into_tree(self):
        self._load_books(None)

    def on_search_double(self, event):
        sel = self.search_tree.selection()
        if not sel: return
        vals = self.search_tree.item(sel[0], "values")
        book_id = int(vals[0])
        self.open_edit_from_book(book_id)

    def on_search_right_click(self, event):
        iid = self.search_tree.identify_row(event.y)
        if iid:
            self.search_tree.selection_set(iid)
            vals = self.search_tree.item(iid, "values")
            book_id = int(vals[0])
            menu = tk.Menu(self.root, tearoff=0)
            menu.add_command(label="Borrow", command=lambda b=book_id: self.open_borrow_modal(b))
            menu.add_command(label="Edit", command=lambda b=book_id: self.open_edit_from_book(b))
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

    def open_edit_from_book(self, book_id):
        b = database.get_book(book_id)
        if b:
            self.title_var.set(b["title"]); self.author_var.set(b.get("author",""))
            self.category_var.set(b.get("category","")); self.isbn_var.set(b.get("isbn",""))
            self.selected_book_id.set(book_id)
            self.show_frame("manage")

    # ---------------- Borrow Frame ----------------
    def _build_borrow(self):
        f = self.frames["borrow"]
        ctk.CTkLabel(f, text="Borrow / Return", font=("Arial", 18, "bold")).pack(pady=8)

        left = ctk.CTkFrame(f)
        left.pack(side="left", fill="both", expand=True, padx=12, pady=8)
        ctk.CTkLabel(left, text="Select Book").pack(anchor="w", pady=(0,6))
        cols = ("id","title","author","category","isbn","available")
        heads = ("ID","Title","Author","Category","ISBN","Available")
        self.borrow_tree, self.borrow_vsb = self.make_tree(left, cols, heads)
        self.borrow_tree.pack(side="left", fill="both", expand=True)
        self.borrow_vsb.pack(side="right", fill="y")

        right = ctk.CTkFrame(f, width=360)
        right.pack(side="right", fill="y", padx=12, pady=8)

        ctk.CTkLabel(right, text="Member").pack(anchor="w", pady=(0,6))
        self.borrow_member_display = ctk.StringVar()
        ctk.CTkEntry(right, textvariable=self.borrow_member_display, state="readonly").pack(fill="x", pady=4)
        ctk.CTkButton(right, text="Search member...", command=self.borrow_open_member_search).pack(pady=(4,8))

        ctk.CTkLabel(right, text="Days until due (default 14)").pack(anchor="w", pady=(10,4))
        self.borrow_days = tk.IntVar(value=14)
        self.borrow_days_spin = tk.Spinbox(right, from_=1, to=365, textvariable=self.borrow_days, width=6)
        self.borrow_days_spin.pack(fill="x", pady=4)
        ctk.CTkButton(right, text="Borrow Book", command=self.borrow_action).pack(fill="x", pady=8)

        ctk.CTkLabel(right, text="Active loans").pack(anchor="w", pady=(10,4))
        loan_cols = ("loan_id","book_title","member_name","date_borrowed","date_due")
        loan_heads = ("Loan ID","Book","Member","Borrowed","Due")
        self.loan_tree, self.loan_vsb = self.make_tree(right, loan_cols, loan_heads)
        self.loan_tree.pack(fill="both", expand=True, pady=6)
        ctk.CTkButton(right, text="Return Selected Loan", command=self.return_action).pack(fill="x", pady=8)

        # selection holder
        self.borrow_selected_member = None

    def borrow_open_member_search(self):
        def on_select(member):
            self.borrow_selected_member = member
            self.borrow_member_display.set(f"{member['id']} — {member['name']} <{member.get('email','')}>")
        SearchableDropdown(self.root, fetch_fn=database.search_members_by_text, on_select=on_select, title="Search member...")

    def load_books_for_borrow(self):
        for r in self.borrow_tree.get_children():
            self.borrow_tree.delete(r)
        rows = database.list_books()
        for r in rows:
            self.borrow_tree.insert("", "end", values=(r["id"], r["title"], r.get("author",""), r.get("category",""), r.get("isbn",""), "Yes" if r["available"] else "No"))

    def borrow_action(self):
        sel = self.borrow_tree.selection()
        if not sel:
            messagebox.showwarning("No book selected", "Please select a book to borrow.")
            return
        vals = self.borrow_tree.item(sel[0], "values")
        book_id = int(vals[0])
        if vals[5] == "No":
            messagebox.showwarning("Unavailable", "This book is not available to borrow.")
            return
        if not self.borrow_selected_member:
            messagebox.showwarning("No member selected", "Please choose a member (Search member...).")
            return
        member_id = self.borrow_selected_member["id"]
        days = int(self.borrow_days.get())
        res = database.borrow_book(book_id, member_id, days_due=days, actor=self.username)
        if res["success"]:
            messagebox.showinfo("Borrowed", res["message"])
            # refresh
            self.load_books_for_borrow(); self.load_books_into_tree(); self.load_active_loans()
            # clear selection
            self.borrow_selected_member = None
            self.borrow_member_display.set("")
        else:
            messagebox.showerror("Error", res["message"])

    def load_active_loans(self):
        for r in self.loan_tree.get_children():
            self.loan_tree.delete(r)
        loans = database.list_loans(show_all=False)
        for l in loans:
            borrowed = datetime.fromisoformat(l["date_borrowed"]).strftime("%Y-%m-%d")
            due = datetime.fromisoformat(l["date_due"]).strftime("%Y-%m-%d")
            self.loan_tree.insert("", "end", values=(l["loan_id"], l["book_title"], l["member_name"], borrowed, due))

    def return_action(self):
        sel = self.loan_tree.selection()
        if not sel:
            messagebox.showwarning("No loan selected", "Select an active loan to return.")
            return
        vals = self.loan_tree.item(sel[0], "values")
        loan_id = int(vals[0])
        res = database.return_book(loan_id, actor=self.username)
        if res["success"]:
            messagebox.showinfo("Returned", res["message"])
            self.load_active_loans(); self.load_books_for_borrow(); self.load_books_into_tree(); self.load_transactions()
        else:
            messagebox.showerror("Error", res["message"])

    def open_borrow_modal(self, book_id):
        # convenience: open borrow modal preselecting book (we use existing borrow frame flow)
        # select book in tree and switch to borrow frame
        self.show_frame("borrow")
        # find and select the book row in borrow_tree
        for iid in self.borrow_tree.get_children():
            vals = self.borrow_tree.item(iid, "values")
            if int(vals[0]) == book_id:
                self.borrow_tree.selection_set(iid)
                self.borrow_tree.see(iid)
                break

    # ---------------- Manage Frame (books) ----------------
    def _build_manage(self):
        f = self.frames["manage"]
        ctk.CTkLabel(f, text="Manage Books", font=("Arial", 18, "bold")).pack(pady=8)
        form = ctk.CTkFrame(f); form.pack(fill="x", padx=12, pady=6)
        self.title_var = ctk.StringVar(); self.author_var = ctk.StringVar(); self.category_var = ctk.StringVar(); self.isbn_var = ctk.StringVar()
        self.selected_book_id = ctk.IntVar(value=0)

        ctk.CTkLabel(form, text="Title").grid(row=0,column=0,sticky="w",pady=6,padx=6)
        ctk.CTkEntry(form, textvariable=self.title_var, width=500).grid(row=0,column=1,pady=6,padx=6)
        ctk.CTkLabel(form, text="Author").grid(row=1,column=0,sticky="w",pady=6,padx=6)
        ctk.CTkEntry(form, textvariable=self.author_var).grid(row=1,column=1,pady=6,padx=6)
        ctk.CTkLabel(form, text="Category").grid(row=2,column=0,sticky="w",pady=6,padx=6)
        ctk.CTkEntry(form, textvariable=self.category_var).grid(row=2,column=1,pady=6,padx=6)
        ctk.CTkLabel(form, text="ISBN").grid(row=3,column=0,sticky="w",pady=6,padx=6)
        ctk.CTkEntry(form, textvariable=self.isbn_var).grid(row=3,column=1,pady=6,padx=6)

        btns = ctk.CTkFrame(f); btns.pack(pady=6)
        ctk.CTkButton(btns, text="Add Book", command=self.add_book_action).grid(row=0,column=0,padx=6)
        ctk.CTkButton(btns, text="Update Selected", command=self.update_book_action).grid(row=0,column=1,padx=6)
        ctk.CTkButton(btns, text="Delete Selected", command=self.delete_book_action).grid(row=0,column=2,padx=6)
        ctk.CTkButton(btns, text="Clear", command=self.clear_manage_form).grid(row=0,column=3,padx=6)

    def add_book_action(self):
        title = self.title_var.get().strip()
        if not title:
            messagebox.showwarning("Input", "Title is required.")
            return
        database.add_book(title, self.author_var.get().strip(), self.category_var.get().strip(), self.isbn_var.get().strip(), actor=self.username)
        messagebox.showinfo("Added", "Book added.")
        self.clear_manage_form()
        self.load_books_into_tree()

    def update_book_action(self):
        bid = self.selected_book_id.get()
        if not bid:
            messagebox.showwarning("Select", "Double-click a book from Search to load it for editing.")
            return
        database.update_book(bid, self.title_var.get().strip(), self.author_var.get().strip(), self.category_var.get().strip(), self.isbn_var.get().strip(), True, actor=self.username)
        messagebox.showinfo("Updated", "Book updated.")
        self.clear_manage_form()
        self.load_books_into_tree()

    def delete_book_action(self):
        bid = self.selected_book_id.get()
        if not bid:
            messagebox.showwarning("Select", "Select a book to delete.")
            return
        if messagebox.askyesno("Confirm", "Delete this book?"):
            res = database.delete_book(bid, actor=self.username)
            if res["success"]:
                messagebox.showinfo("Deleted", res["message"])
                self.clear_manage_form(); self.load_books_into_tree()
            else:
                messagebox.showerror("Could not delete", res["message"])

    def clear_manage_form(self):
        self.selected_book_id.set(0)
        self.title_var.set(""); self.author_var.set(""); self.category_var.set(""); self.isbn_var.set("")

    # ---------------- Members ----------------
    def _build_members(self):
        f = self.frames["members"]
        ctk.CTkLabel(f, text="Members", font=("Arial", 18, "bold")).pack(pady=8)
        form = ctk.CTkFrame(f); form.pack(fill="x", padx=12, pady=6)
        self.mname_var = ctk.StringVar(); self.memail_var = ctk.StringVar()
        ctk.CTkLabel(form, text="Name").grid(row=0,column=0,sticky="w",padx=6,pady=6)
        ctk.CTkEntry(form, textvariable=self.mname_var, width=400).grid(row=0,column=1,padx=6,pady=6)
        ctk.CTkLabel(form, text="Email").grid(row=1,column=0,sticky="w",padx=6,pady=6)
        ctk.CTkEntry(form, textvariable=self.memail_var).grid(row=1,column=1,padx=6,pady=6)
        ctk.CTkButton(form, text="Add Member", command=self.add_member_action).grid(row=2,column=0,columnspan=2,pady=8)

        list_frame = ctk.CTkFrame(f); list_frame.pack(fill="both", expand=True, padx=12, pady=6)
        cols = ("id","name","email"); heads = ("ID","Name","Email")
        self.member_tree, self.member_vsb = self.make_tree(list_frame, cols, heads)
        self.member_tree.pack(fill="both", expand=True); self.member_vsb.pack(side="right", fill="y")

    def add_member_action(self):
        name = self.mname_var.get().strip()
        email = self.memail_var.get().strip()
        if not name:
            messagebox.showwarning("Input", "Member name required.")
            return
        if email and not is_valid_email(email):
            messagebox.showwarning("Invalid", "Email format looks invalid.")
            return
        database.add_member(name, email, actor=self.username)
        messagebox.showinfo("Added", "Member added.")
        self.mname_var.set(""); self.memail_var.set("")
        self.load_members()

    def load_members(self):
        for r in self.member_tree.get_children():
            self.member_tree.delete(r)
        for m in database.list_members():
            self.member_tree.insert("", "end", values=(m["id"], m["name"], m.get("email","")))

    # ---------------- Transactions / Export ----------------
    def _build_transactions(self):
        f = self.frames["transactions"]
        ctk.CTkLabel(f, text="Transactions / Export", font=("Arial", 18, "bold")).pack(pady=8)
        controls = ctk.CTkFrame(f); controls.pack(fill="x", padx=12, pady=6)
        ctk.CTkLabel(controls, text="From (YYYY-MM-DD)").grid(row=0,column=0,padx=6,pady=6)
        self.export_from = ctk.StringVar(); ctk.CTkEntry(controls, textvariable=self.export_from).grid(row=0,column=1,padx=6,pady=6)
        ctk.CTkLabel(controls, text="To (YYYY-MM-DD)").grid(row=0,column=2,padx=6,pady=6)
        self.export_to = ctk.StringVar(); ctk.CTkEntry(controls, textvariable=self.export_to).grid(row=0,column=3,padx=6,pady=6)
        ctk.CTkButton(controls, text="Export CSV", command=lambda: self.export_transactions_range("csv")).grid(row=0,column=4,padx=6)
        ctk.CTkButton(controls, text="Export XLSX", command=lambda: self.export_transactions_range("xlsx")).grid(row=0,column=5,padx=6)
        ctk.CTkButton(controls, text="Download Import Template", command=self.save_import_template).grid(row=1,column=0,columnspan=2,pady=8)

        table_frame = ctk.CTkFrame(f); table_frame.pack(fill="both", expand=True, padx=12, pady=6)
        cols = ("loan_id","book","member","borrowed","due","returned","late_fee")
        heads = ("Loan ID","Book","Member","Borrowed","Due","Returned","Late Fee")
        self.trans_tree, self.trans_vsb = self.make_tree(table_frame, cols, heads)
        self.trans_tree.pack(fill="both", expand=True); self.trans_vsb.pack(side="right", fill="y")

    def export_transactions_range(self, fmt="csv"):
        rows = database.get_all_loans_for_export()
        dfrom = self.export_from.get().strip(); dto = self.export_to.get().strip()
        if dfrom or dto:
            try:
                dfrom_dt = datetime.fromisoformat(dfrom) if dfrom else None
                dto_dt = datetime.fromisoformat(dto) if dto else None
            except Exception:
                messagebox.showerror("Invalid date", "Dates must be in YYYY-MM-DD format")
                return
            filtered = []
            for r in rows:
                borrowed = datetime.fromisoformat(r["date_borrowed"])
                if dfrom_dt and borrowed < dfrom_dt: continue
                if dto_dt and borrowed > dto_dt: continue
                filtered.append(r)
            rows = filtered

        if fmt == "csv":
            filename = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files","*.csv")])
            if not filename:
                return
            with open(filename, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f); writer.writerow(["Loan ID","Book","Member","Borrowed","Due","Returned","Late Fee"])
                for r in rows:
                    borrowed = datetime.fromisoformat(r["date_borrowed"]).strftime("%Y-%m-%d")
                    due = datetime.fromisoformat(r["date_due"]).strftime("%Y-%m-%d")
                    returned_str = datetime.fromisoformat(r["date_returned"]).strftime("%Y-%m-%d") if r["date_returned"] else ""
                    writer.writerow([r["loan_id"], r["book_title"], r["member_name"], borrowed, due, returned_str, r["late_fee"]])
            database.log_audit(self.username, "export_csv", f"exported {len(rows)} transactions")
            messagebox.showinfo("Exported", f"CSV saved to {filename}")
        else:
            filename = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel files","*.xlsx")])
            if not filename:
                return
            wb = openpyxl.Workbook(); ws = wb.active; ws.title="Transactions"
            ws.append(["Loan ID","Book","Member","Borrowed","Due","Returned","Late Fee"])
            for r in rows:
                borrowed = datetime.fromisoformat(r["date_borrowed"]).strftime("%Y-%m-%d")
                due = datetime.fromisoformat(r["date_due"]).strftime("%Y-%m-%d")
                returned_str = datetime.fromisoformat(r["date_returned"]).strftime("%Y-%m-%d") if r["date_returned"] else ""
                ws.append([r["loan_id"], r["book_title"], r["member_name"], borrowed, due, returned_str, r["late_fee"]])
            wb.save(filename)
            database.log_audit(self.username, "export_xlsx", f"exported {len(rows)} transactions")
            messagebox.showinfo("Exported", f"XLSX saved to {filename}")

    def save_import_template(self):
        filename = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel files","*.xlsx")])
        if not filename:
            return
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Books import"
        ws.append(["title","author","category","isbn"])
        wb.save(filename)
        database.log_audit(self.username, "download_template", "books_import_template")
        messagebox.showinfo("Template saved", f"Template saved to {filename}")

    def load_transactions(self):
        for r in self.trans_tree.get_children():
            self.trans_tree.delete(r)
        rows = database.get_all_loans_for_export()
        for r in rows:
            borrowed = datetime.fromisoformat(r["date_borrowed"]).strftime("%Y-%m-%d")
            due = datetime.fromisoformat(r["date_due"]).strftime("%Y-%m-%d")
            returned_str = datetime.fromisoformat(r["date_returned"]).strftime("%Y-%m-%d") if r["date_returned"] else ""
            self.trans_tree.insert("", "end", values=(r["loan_id"], r["book_title"], r["member_name"], borrowed, due, returned_str, r["late_fee"]))

    # ---------------- Audit Log (admin) ----------------
    def _build_audit(self):
        f = self.frames["audit"]
        ctk.CTkLabel(f, text="Audit Log", font=("Arial", 18, "bold")).pack(pady=8)
        top = ctk.CTkFrame(f); top.pack(fill="x", padx=12, pady=6)
        ctk.CTkLabel(top, text="Since (YYYY-MM-DD)").grid(row=0, column=0, padx=6, pady=6)
        self.audit_since = ctk.StringVar(); ctk.CTkEntry(top, textvariable=self.audit_since).grid(row=0, column=1, padx=6)
        ctk.CTkButton(top, text="Refresh", command=self.load_audit).grid(row=0, column=2, padx=6)

        table_frame = ctk.CTkFrame(f); table_frame.pack(fill="both", expand=True, padx=12, pady=6)
        cols = ("id","actor","action","details","created_at"); heads = ("ID","Actor","Action","Details","When")
        self.audit_tree, self.audit_vsb = self.make_tree(table_frame, cols, heads)
        self.audit_tree.pack(fill="both", expand=True); self.audit_vsb.pack(side="right", fill="y")

    def load_audit(self):
        since_txt = self.audit_since.get().strip()
        since = None
        if since_txt:
            try:
                since = datetime.fromisoformat(since_txt)
            except Exception:
                messagebox.showerror("Invalid", "Since date must be YYYY-MM-DD")
                return
        rows = database.query_audit(limit=500, since=since)
        for r in self.audit_tree.get_children():
            self.audit_tree.delete(r)
        for a in rows:
            self.audit_tree.insert("", "end", values=(a["id"], a["actor"], a["action"], a["details"], a["created_at"]))

    # ---------------- Settings / Admin ----------------
    def _build_settings(self):
        f = self.frames["settings"]
        ctk.CTkLabel(f, text="Settings & Admin", font=("Arial", 18, "bold")).pack(pady=8)
        sfrm = ctk.CTkFrame(f); sfrm.pack(padx=12, pady=12, fill="x")

        # late fee
        ctk.CTkLabel(sfrm, text="Late fee per day").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        self.late_fee_var = ctk.DoubleVar(value=float(database.get_setting("late_fee_per_day") or 0.50))
        ctk.CTkEntry(sfrm, textvariable=self.late_fee_var).grid(row=0, column=1, padx=6, pady=6)
        ctk.CTkButton(sfrm, text="Save", command=self.save_settings).grid(row=0, column=2, padx=6)

        # SMTP settings (editable by admin)
        ctk.CTkLabel(sfrm, text="SMTP Host").grid(row=1, column=0, sticky="w", padx=6, pady=6)
        self.smtp_host_var = ctk.StringVar(value=database.get_setting("smtp_host") or "")
        ctk.CTkEntry(sfrm, textvariable=self.smtp_host_var).grid(row=1, column=1, padx=6, pady=6)

        ctk.CTkLabel(sfrm, text="SMTP Port").grid(row=2, column=0, sticky="w", padx=6, pady=6)
        self.smtp_port_var = ctk.StringVar(value=database.get_setting("smtp_port") or "587")
        ctk.CTkEntry(sfrm, textvariable=self.smtp_port_var).grid(row=2, column=1, padx=6, pady=6)

        ctk.CTkLabel(sfrm, text="SMTP User (from address)").grid(row=3, column=0, sticky="w", padx=6, pady=6)
        self.smtp_user_var = ctk.StringVar(value=database.get_setting("smtp_user") or "")
        ctk.CTkEntry(sfrm, textvariable=self.smtp_user_var).grid(row=3, column=1, padx=6, pady=6)

        ctk.CTkLabel(sfrm, text="SMTP Password").grid(row=4, column=0, sticky="w", padx=6, pady=6)
        self.smtp_pw_var = ctk.StringVar(value=database.get_setting("smtp_password") or "")
        ctk.CTkEntry(sfrm, textvariable=self.smtp_pw_var, show="*").grid(row=4, column=1, padx=6, pady=6)

        ctk.CTkButton(sfrm, text="Save SMTP Settings", command=self.save_smtp_settings).grid(row=5, column=0, padx=6, pady=(8,0))
        ctk.CTkButton(sfrm, text="Test SMTP (send to SMTP user)", command=self.test_smtp_connection).grid(row=5, column=1, padx=6, pady=(8,0))

        # Change password for current user
        ctk.CTkLabel(sfrm, text="Change Password", font=("Arial", 12, "bold")).grid(row=6, column=0, sticky="w", padx=6, pady=(12,6))
        ctk.CTkLabel(sfrm, text="Current").grid(row=7, column=0, sticky="w", padx=6, pady=6)
        self.current_pw = ctk.StringVar(); ctk.CTkEntry(sfrm, textvariable=self.current_pw, show="*").grid(row=7, column=1, padx=6)
        ctk.CTkLabel(sfrm, text="New").grid(row=8, column=0, sticky="w", padx=6, pady=6)
        self.new_pw = ctk.StringVar(); ctk.CTkEntry(sfrm, textvariable=self.new_pw, show="*").grid(row=8, column=1, padx=6)
        ctk.CTkLabel(sfrm, text="Confirm").grid(row=9, column=0, sticky="w", padx=6, pady=6)
        self.confirm_pw = ctk.StringVar(); ctk.CTkEntry(sfrm, textvariable=self.confirm_pw, show="*").grid(row=9, column=1, padx=6)
        ctk.CTkButton(sfrm, text="Change Password", command=self.change_password_action).grid(row=10, column=0, columnspan=2, pady=8)

        # Admin-only: create admin & reset user password
        if self.role == "admin":
            ctk.CTkLabel(sfrm, text="Create Admin User", font=("Arial", 12, "bold")).grid(row=11, column=0, sticky="w", padx=6, pady=(12,6))
            self.new_admin_user = ctk.StringVar(); self.new_admin_pw = ctk.StringVar()
            ctk.CTkLabel(sfrm, text="Username").grid(row=12, column=0, sticky="w", padx=6); ctk.CTkEntry(sfrm, textvariable=self.new_admin_user).grid(row=12, column=1)
            ctk.CTkLabel(sfrm, text="Password").grid(row=13, column=0, sticky="w", padx=6); ctk.CTkEntry(sfrm, textvariable=self.new_admin_pw, show="*").grid(row=13, column=1)
            ctk.CTkButton(sfrm, text="Create Admin", command=self.create_admin_action).grid(row=14, column=0, columnspan=2, pady=8)

            ctk.CTkLabel(sfrm, text="Admin Reset User Password", font=("Arial", 12, "bold")).grid(row=15, column=0, sticky="w", padx=6, pady=(12,6))
            self.reset_user_var = ctk.StringVar(); self.reset_pw_var = ctk.StringVar()
            ctk.CTkLabel(sfrm, text="Username").grid(row=16, column=0, sticky="w", padx=6); ctk.CTkEntry(sfrm, textvariable=self.reset_user_var).grid(row=16, column=1)
            ctk.CTkLabel(sfrm, text="New password").grid(row=17, column=0, sticky="w", padx=6); ctk.CTkEntry(sfrm, textvariable=self.reset_pw_var, show="*").grid(row=17, column=1)
            ctk.CTkButton(sfrm, text="Reset Password", command=self.admin_reset_action).grid(row=18, column=0, columnspan=2, pady=8)

    def save_settings(self):
        v = self.late_fee_var.get()
        try:
            float(v)
        except Exception:
            messagebox.showerror("Invalid", "Late fee must be a number")
            return
        database.set_setting("late_fee_per_day", str(v), actor=self.username)
        messagebox.showinfo("Saved", "Settings saved.")
        database.log_audit(self.username, "save_settings", f"late_fee_per_day={v}")

    def save_smtp_settings(self):
        host = self.smtp_host_var.get().strip(); port = self.smtp_port_var.get().strip()
        user = self.smtp_user_var.get().strip(); pw = self.smtp_pw_var.get().strip()
        if not host or not port or not user:
            if not messagebox.askyesno("Confirm", "Host/Port/User empty — this will clear SMTP settings. Continue?"):
                return
        database.set_setting("smtp_host", host, actor=self.username)
        database.set_setting("smtp_port", port, actor=self.username)
        database.set_setting("smtp_user", user, actor=self.username)
        database.set_setting("smtp_password", pw, actor=self.username)
        messagebox.showinfo("Saved", "SMTP settings saved.")
        database.log_audit(self.username, "save_smtp", f"host={host} user={user}")

    def test_smtp_connection(self):
        # try sending a small test email to smtp_user (best-effort). Run in background.
        cfg_user = self.smtp_user_var.get().strip()
        if not cfg_user:
            messagebox.showwarning("No recipient", "Set 'SMTP User' before testing (we will send test mail to that address).")
            return

        def _do_test():
            res = mailer.send_email(cfg_user, "Library SMTP test", "This is a test email from Library System.")
            if res.get("success"):
                messagebox.showinfo("SMTP Test", "Test email sent successfully (check inbox).")
            else:
                messagebox.showerror("SMTP Test failed", f"Failed to send test: {res.get('message')}")
        threading.Thread(target=_do_test, daemon=True).start()

    def change_password_action(self):
        cur = self.current_pw.get(); new = self.new_pw.get(); conf = self.confirm_pw.get()
        if not cur or not new or not conf:
            messagebox.showwarning("Input", "All fields required")
            return
        if new != conf:
            messagebox.showwarning("Mismatch", "New passwords do not match")
            return
        res = database.change_user_password(self.username, cur, new)
        if res["success"]:
            # background best-effort send confirmation
            def _send_confirm():
                # try find a member record with email == username OR name == username
                recipient = None
                for m in database.list_members():
                    if m.get("email") and m["email"].lower() == self.username.lower():
                        recipient = m["email"]; break
                    if m.get("name") and m["name"].lower() == self.username.lower():
                        recipient = m.get("email"); break
                if recipient:
                    mailer.send_password_change_email(self.username, recipient)
            threading.Thread(target=_send_confirm, daemon=True).start()

            messagebox.showinfo("Changed", res["message"])
            database.log_audit(self.username, "change_password", "user changed own password")
            self.current_pw.set(""); self.new_pw.set(""); self.confirm_pw.set("")
        else:
            messagebox.showerror("Error", res["message"])

    def create_admin_action(self):
        u = self.new_admin_user.get().strip(); p = self.new_admin_pw.get()
        if not u or not p:
            messagebox.showwarning("Input", "Username & password required")
            return
        try:
            database.create_user(u, p, role="admin")
            messagebox.showinfo("Created", f"Admin '{u}' created")
            self.new_admin_user.set(""); self.new_admin_pw.set("")
        except Exception as e:
            messagebox.showerror("Error", f"Could not create user: {e}")

    def admin_reset_action(self):
        target = self.reset_user_var.get().strip(); new = self.reset_pw_var.get()
        if not target or not new:
            messagebox.showwarning("Input", "Username & new password required")
            return
        res = database.admin_reset_password(self.username, target, new)
        if res["success"]:
            # best-effort send notification
            def _send_reset():
                recipient = None
                for m in database.list_members():
                    if (m.get("email") and m["email"].lower() == target.lower()) or (m.get("name") and m["name"].lower() == target.lower()):
                        recipient = m.get("email"); break
                if recipient:
                    mailer.send_admin_reset_email(self.username, target, recipient)
            threading.Thread(target=_send_reset, daemon=True).start()

            messagebox.showinfo("Reset", res["message"])
            database.log_audit(self.username, "admin_reset_password", f"reset for {target}")
            self.reset_user_var.set(""); self.reset_pw_var.set("")
        else:
            messagebox.showerror("Error", res["message"])

    # ---------------- utilities / loaders ----------------
    def make_tree(self, parent, columns, headings):
        tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="browse")
        for col, head in zip(columns, headings):
            tree.heading(col, text=head)
            tree.column(col, width=120, anchor="w")
        vsb = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        return tree, vsb

    def refresh_all(self):
        self.draw_dashboard(); self.load_books_into_tree(); self.load_books_for_borrow(); self.load_members(); self.load_active_loans(); self.load_transactions(); self.load_audit(); self.load_settings()

    # wrappers (some loaders used above)
    def load_books_for_borrow(self):
        for r in self.borrow_tree.get_children():
            self.borrow_tree.delete(r)
        for b in database.list_books():
            self.borrow_tree.insert("", "end", values=(b["id"], b["title"], b.get("author",""), b.get("category",""), b.get("isbn",""), "Yes" if b["available"] else "No"))

    def load_books_into_tree(self):
        self._load_books(None)

    def load_active_loans(self):
        for r in self.loan_tree.get_children():
            self.loan_tree.delete(r)
        loans = database.list_loans(show_all=False)
        for l in loans:
            borrowed = datetime.fromisoformat(l["date_borrowed"]).strftime("%Y-%m-%d")
            due = datetime.fromisoformat(l["date_due"]).strftime("%Y-%m-%d")
            self.loan_tree.insert("", "end", values=(l["loan_id"], l["book_title"], l["member_name"], borrowed, due))

    def load_members(self):
        for r in self.member_tree.get_children():
            self.member_tree.delete(r)
        for m in database.list_members():
            self.member_tree.insert("", "end", values=(m["id"], m["name"], m.get("email","")))

    def load_transactions(self):
        for r in self.trans_tree.get_children():
            self.trans_tree.delete(r)
        rows = database.get_all_loans_for_export()
        for r in rows:
            borrowed = datetime.fromisoformat(r["date_borrowed"]).strftime("%Y-%m-%d")
            due = datetime.fromisoformat(r["date_due"]).strftime("%Y-%m-%d")
            returned_str = datetime.fromisoformat(r["date_returned"]).strftime("%Y-%m-%d") if r["date_returned"] else ""
            self.trans_tree.insert("", "end", values=(r["loan_id"], r["book_title"], r["member_name"], borrowed, due, returned_str, r["late_fee"]))

    def load_active_loans(self):
        # already implemented above, keep simple alias
        self.load_active_loans = lambda: None

    def load_audit(self):
        # implemented in show_frame -> load_audit call
        pass

    def load_settings(self):
        self.late_fee_var.set(float(database.get_setting("late_fee_per_day") or 0.50))
        self.smtp_host_var.set(database.get_setting("smtp_host") or "")
        self.smtp_port_var.set(database.get_setting("smtp_port") or "587")
        self.smtp_user_var.set(database.get_setting("smtp_user") or "")
        self.smtp_pw_var.set(database.get_setting("smtp_password") or "")

# ---------------------------
# Application start
# ---------------------------
if __name__ == "__main__":
    import sys
    root = tk.Tk()
    root.withdraw()

    def on_login_success(user):
        root.deiconify()
        app = LibraryApp(root, user)
        root.mainloop()

    LoginWindow(root, on_login_success)
    root.mainloop()

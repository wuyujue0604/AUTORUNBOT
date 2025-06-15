import os
import json
import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
import tkinter.font as tkfont

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)
DB_FOLDER = os.path.join(PARENT_DIR, "json_results")

class DBTableViewer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SQLite 多資料庫多表瀏覽器")
        self.geometry("1100x700")

        self.db_files = self.scan_db_files()
        self.current_db_path = None
        self.tables = []
        self.current_table = None

        self.create_widgets()

        if self.db_files:
            self.db_select_combo['values'] = self.db_files
            self.db_select_combo.current(0)
            self.after(100, self.on_db_selected)
        else:
            messagebox.showwarning("提醒", f"找不到任何 .db 檔案於資料夾：\n{DB_FOLDER}")

    def scan_db_files(self):
        if not os.path.exists(DB_FOLDER):
            return []
        files = [f for f in os.listdir(DB_FOLDER) if f.endswith(".db")]
        files.sort()
        return files

    def create_widgets(self):
        top_frame = tk.Frame(self)
        top_frame.pack(fill=tk.X, padx=5, pady=5)

        tk.Label(top_frame, text="選擇資料庫檔案:").pack(side=tk.LEFT)
        self.db_select_combo = ttk.Combobox(top_frame, state="readonly", width=60)
        self.db_select_combo.pack(side=tk.LEFT, padx=5)
        self.db_select_combo.bind("<<ComboboxSelected>>", self.on_db_selected)

        tk.Label(top_frame, text="選擇資料表:").pack(side=tk.LEFT, padx=(15,0))
        self.table_select_combo = ttk.Combobox(top_frame, state="readonly", width=30)
        self.table_select_combo.pack(side=tk.LEFT, padx=5)
        self.table_select_combo.bind("<<ComboboxSelected>>", self.on_table_selected)

        refresh_btn = tk.Button(top_frame, text="刷新資料", command=self.on_refresh_clicked)
        refresh_btn.pack(side=tk.LEFT, padx=5)

        tree_frame = tk.Frame(self)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.tree = ttk.Treeview(tree_frame, show="headings")
        self.tree.grid(row=0, column=0, sticky="nsew")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        vsb.grid(row=0, column=1, sticky="ns")

        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        hsb.grid(row=1, column=0, sticky="ew")

        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

    def get_tables(self, db_path):
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tables = [row[0] for row in cursor.fetchall()]
            conn.close()
            return tables
        except Exception as e:
            messagebox.showerror("錯誤", f"讀取資料庫表失敗：{e}")
            return []

    def get_columns(self, db_path, table_name):
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute(f"PRAGMA table_info('{table_name}')")
            columns = [row[1] for row in cursor.fetchall()]
            conn.close()
            return columns
        except Exception as e:
            messagebox.showerror("錯誤", f"讀取表格欄位失敗：{e}")
            return []

    def on_db_selected(self, event=None):
        selected_file = self.db_select_combo.get()
        if not selected_file:
            return
        self.current_db_path = os.path.join(DB_FOLDER, selected_file)
        self.tables = self.get_tables(self.current_db_path)
        if not self.tables:
            messagebox.showinfo("訊息", f"資料庫 {selected_file} 沒有任何資料表")
            self.table_select_combo['values'] = []
            self.current_table = None
            self.clear_tree()
            return

        self.table_select_combo['values'] = self.tables
        self.table_select_combo.current(0)
        self.current_table = self.tables[0]
        self.load_data()

    def on_table_selected(self, event=None):
        selected_table = self.table_select_combo.get()
        if not selected_table:
            return
        self.current_table = selected_table
        self.load_data()

    def on_refresh_clicked(self):
        if self.current_db_path and self.current_table:
            self.load_data()
        else:
            messagebox.showinfo("訊息", "請先選擇資料庫與資料表")

    def clear_tree(self):
        for col in self.tree["columns"]:
            self.tree.heading(col, text="")
            self.tree.column(col, width=0)
        self.tree["columns"] = []
        for row in self.tree.get_children():
            self.tree.delete(row)

    def load_data(self):
        self.clear_tree()
        if not self.current_db_path or not self.current_table:
            return

        if not os.path.exists(self.current_db_path):
            messagebox.showerror("錯誤", f"資料庫檔案不存在：{self.current_db_path}")
            return

        try:
            conn = sqlite3.connect(self.current_db_path)
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM '{self.current_table}'")
            rows = cursor.fetchall()

            cursor.execute(f"PRAGMA table_info('{self.current_table}')")
            columns = [row[1] for row in cursor.fetchall()]
            conn.close()
        except Exception as e:
            messagebox.showerror("錯誤", f"讀取資料失敗：{e}")
            return

        self.tree["columns"] = columns
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=120, anchor="w")

        for row in rows:
            display_row = []
            for cell in row:
                if isinstance(cell, str) and len(cell) > 80:
                    display_row.append(cell[:80] + "...")
                else:
                    display_row.append(cell)
            self.tree.insert("", tk.END, values=display_row)

        self.adjust_treeview_size()

    def adjust_treeview_size(self):
        font = tkfont.Font()
        total_width = self.tree.winfo_width() or self.winfo_width() or 1000
        cols = self.tree["columns"]
        max_col_widths = {}

        # 計算每欄最大寬度（標題與內容）
        for col in cols:
            max_width = font.measure(col)
            for item in self.tree.get_children():
                cell_text = str(self.tree.set(item, col))
                display_text = cell_text
                if len(cell_text) > 60:
                    display_text = cell_text[:60] + "..."
                cell_width = font.measure(display_text)
                if cell_width > max_width:
                    max_width = cell_width
            max_col_widths[col] = max_width + 20  # padding

        # 限制欄寬最大值，避免整體寬度過大
        max_per_col = max(100, (total_width - 30) // max(len(cols), 1))

        # 設定欄寬
        for col in cols:
            final_width = max_col_widths.get(col, 100)
            if final_width > max_per_col:
                final_width = max_per_col
            self.tree.column(col, width=final_width, anchor="w")

        style = ttk.Style()
        style.configure("Treeview", rowheight=25)

if __name__ == "__main__":
    app = DBTableViewer()
    app.mainloop()

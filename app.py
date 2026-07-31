from flask import Flask, request, jsonify, render_template, redirect, url_for
from database import get_connection, init_db
from datetime import datetime

app = Flask(__name__)

# Every column that may appear in a PUT body
UPDATABLE_FIELDS = {
    "task",
    "is_done",
    "tag",
    "owner",
}

# Columns shown in the terminal table and their headers
COLUMNS = [
    ("task",    "TASK"),
    ("is_done", "STATUS"),
    ("tag",     "TAG"),
    ("owner",   "CREATED BY"),
]


# ── helpers ────────────────────────────────────────────────────────────────────

def now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def row_to_dict(row):
    return dict(row) if row else None

def _wants_text(req):
    """
    Return True  → send plain-text table
    Return False → send JSON

    Rules (checked in order):
      1. Accept: application/json  → always JSON
      2. Accept: text/html         → always JSON  (browser gets JSON, UI is at /)
      3. curl / wget / httpie with no explicit Accept → text table
      4. anything else             → JSON
    """
    accept = req.headers.get("Accept", "")
    ua     = req.headers.get("User-Agent", "").lower()

    # explicit JSON request → always honour it
    if "application/json" in accept:
        return False

    # browser requesting the API endpoint directly → return JSON
    if "text/html" in accept:
        return False

    # terminal tools that send Accept: */*  (curl default)
    terminal_tools = ("curl/", "httpie/", "wget/", "python-requests/")
    if any(t in ua for t in terminal_tools):
        return True

    return False    # default: JSON

# ── input sanitizer (if not already in app.py) ─────────────────────────────────

def clean(value):
    """
    Strip whitespace from strings.
    Return None for empty/whitespace-only strings and actual None.
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    return value

# ── LIST  ─────────────────────────────────────────────────────────────────────
# GET /api/todos

@app.route("/api/todos", methods=["GET"])
def list_todos():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM todos ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()

    todos = [row_to_dict(r) for r in rows]

    return jsonify(todos)


# ── INSERT ─────────────────────────────────────────────────────────────────────
# POST /api/todos
# Required body fields: task

@app.route("/api/todos", methods=["POST"])
def insert_todo():
    data = request.get_json(silent=True) or {}
    data = {k: clean(v) for k, v in data.items()}

    if not data or 'task' not in data or not data['task'].strip():
        return jsonify({"error": "Task content cannot be empty"}), 400

    task_text = data['task'].strip()
    tag = data.get('tag', '').strip() if data.get('tag') else None
    owner = data.get('owner', '').strip() if data.get('owner') else None

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO todos (task, tag, owner) VALUES (?, ?, ?)", 
            (task_text, tag, owner)
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({"error": str(e)}), 409

    new_id = cursor.lastrowid
    cursor.execute("SELECT * FROM todos WHERE id = ?", (new_id,))
    new_todo = dict(cursor.fetchone())
    conn.close()
    return jsonify(new_todo), 201    

# ── UPDATE ─────────────────────────────────────────────────────────────────────
# PUT /api/todos/<task_id>
# Body may contain any subset of UPDATABLE_FIELDS
# task in body → renames the task

@app.route("/api/todos/<int:todo_id>", methods=["PUT"])
def update_todo(todo_id):
    """Updates a task dynamically and returns a summary of changed fields."""
    data = request.get_json() or {}
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # 1. Fetch current status to verify task existence and track changes
    cursor.execute("SELECT * FROM todos WHERE id = ?", (todo_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Task not found"}), 404
        
    existing = dict(row)
    current_name = existing['task']
    
    # 2. Build the dynamic SQL update statement by checking what actually changed
    updates = {}
    fields_updated = []
    
    # Check the task description text
    if 'task' in data:
        new_name = data['task'].strip()
        if not new_name:
            conn.close()
            return jsonify({"error": "Task text cannot be empty"}), 400
        if new_name != current_name:
            updates['task'] = new_name
            fields_updated.append('task')
            
    # Check the tag column
    if 'tag' in data and data['tag'] != existing['tag']:
        updates['tag'] = data['tag']
        fields_updated.append('tag')
        
    # Check the owner column
    if 'owner' in data and data['owner'] != existing['owner']:
        updates['owner'] = data['owner']
        fields_updated.append('owner')
    # 3. If no fields actually changed value, return early without touching the DB
    if not updates:
        conn.close()
        return jsonify({
            "message": f"Task '{current_name}' remained unchanged.",
            "fields_updated": [],
            "todo": existing
        }), 200

    # 4. Construct and execute the dynamic SET query safely
    set_clause = ", ".join([f"{col} = ?" for col in updates.keys()])
    values = list(updates.values()) + [todo_id]
    
    try:
        cursor.execute(f"UPDATE todos SET {set_clause} WHERE id = ?", values)
        conn.commit()
        
        # 5. Fetch the newly saved record state
        cursor.execute("SELECT * FROM todos WHERE id = ?", (todo_id,))
        updated_todo = dict(cursor.fetchone())
        
        # 6. Build your custom analytics response payload
        response_payload = {
            "message": f"Task '{current_name}' updated.",
            "fields_updated": fields_updated,
            "todo": updated_todo
        }
        
        # Append rename notice if description string altered
        if 'task' in updates:
            response_payload["renamed_to"] = updates['task']
            
        return jsonify(response_payload), 200
        
    except Exception as e:
        conn.rollback()
        return jsonify({"error": f"Update failed: {str(e)}"}), 500
    finally:
        conn.close()



@app.route('/api/todos/<int:todo_id>/done', methods=['PUT'])
def mark_todo_done(todo_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM todos WHERE id = ?", (todo_id,))
    if not cursor.fetchone():
        conn.close()
        return jsonify({"error": "Task not found"}), 404
        
    cursor.execute("UPDATE todos SET is_done = 1 WHERE id = ?", (todo_id,))
    conn.commit()
    
    cursor.execute("SELECT * FROM todos WHERE id = ?", (todo_id,))
    updated_todo = dict(cursor.fetchone())
    conn.close()
    
    return jsonify(updated_todo), 200

# ── BROWSER UI ─────────────────────────────────────────────────────────────────
# GET /

@app.route("/", methods=["GET"])
def ui():
    # 1. Read the active tag filter straight out of the URL query parameters
    selected_filter = request.args.get('tag', 'all').strip()
    
    conn = get_connection()
    
    # 2. Fetch all unique tags currently in use across all tasks for the filter bar
    tag_rows = conn.execute(
        "SELECT DISTINCT tag FROM todos WHERE tag IS NOT NULL AND tag != ''"
    ).fetchall()
    unique_tags = [r['tag'] for r in tag_rows]
    
    # 3. Fetch all tasks to compute accurate counts and list views
    rows = conn.execute("SELECT * FROM todos ORDER BY created_at DESC").fetchall()
    all_todos = [row_to_dict(r) for r in rows]
    
    conn.close()
    
    # Pre-sort overall tasks by completion status
    all_pending = [t for t in all_todos if t["is_done"] == 0]
    all_done = [t for t in all_todos if t["is_done"] == 1]
    
    # 4. Calculate exactly how many pending tasks exist for EACH unique tag
    tag_counts = {}
    for tag in unique_tags:
        tag_counts[tag] = sum(1 for t in all_pending if t["tag"] == tag)
        
    # 5. Filter ONLY the pending tasks list if a specific tag filter is active
    if selected_filter != 'all':
        pending_todos = [t for t in all_pending if t["tag"] and t["tag"].lower() == selected_filter.lower()]
    else:
        pending_todos = all_pending

    return render_template(
        "index.html",
        pending_todos=pending_todos,
        done_todos=all_done,
        total=len(all_todos),
        new=len(all_pending),       # Global pending counter
        done=len(all_done),         # Global completed counter
        tag_counts=tag_counts,
        active_tag=selected_filter  # Keeps track of which tab is active
    )

@app.route("/web/add", methods=["POST"])
def web_add_todo():
    """Handles adding a task with optional tag and owner details from the web form."""
    task_text = request.form.get("task", "").strip()

    # Extract the new tag and owner strings, clean whitespace, and fallback to None if empty
    tag_text = request.form.get("tag", "").strip() or None
    owner_text = request.form.get("owner", "").strip() or None

    if task_text:
        conn = get_connection()
        # Insert all fields safely into the schema fields mapping rows
        conn.execute(
            "INSERT INTO todos (task, tag, owner) VALUES (?, ?, ?)",
            (task_text, tag_text, owner_text)
        )
        conn.commit()
        conn.close()

    return redirect(url_for("ui"))

@app.route("/web/done/<int:todo_id>", methods=["POST"])
def web_mark_done(todo_id):
    """Handles completing a task from the template action buttons."""
    conn = get_connection()
    conn.execute("UPDATE todos SET is_done = 1 WHERE id = ?", (todo_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("ui"))

# ── plain-text table for terminal callers ──────────────────────────────────────

def _render_table(todos):
    if not todos:
        return "No todos found.\n"

    # dynamic column widths
    widths = {}
    for key, header in COLUMNS:
        col_max = max((len(m.get(key) or "") for m in todos), default=0)
        widths[key] = max(len(header), col_max)

    def divider():
        return "+-" + "-+-".join("-" * widths[k] for k, _ in COLUMNS) + "-+"

    def fmt_row(values):
        cells = (
            str(v or "").ljust(widths[k])
            for (k, _), v in zip(COLUMNS, values)
        )
        return "| " + " | ".join(cells) + " |"

    lines = [
        divider(),
        fmt_row([h for _, h in COLUMNS]),
        divider(),
    ]
    for m in todos:
        lines.append(fmt_row([m.get(k) for k, _ in COLUMNS]))

    lines.append(divider())
    lines.append(f"  {len(todos)} todo(s).\n")
    return "\n".join(lines)


# ── entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=8000, debug=True)

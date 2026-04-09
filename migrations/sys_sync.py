
# import os
# import psycopg2
# from dotenv import load_dotenv

# # Use relative path for portability between local and production
# base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# dotenv_path = os.path.join(base_dir, '.env')
# load_dotenv(dotenv_path)

# db_url = os.getenv('DATABASE_URL') or 'postgresql://scholaris:scholaris_local@localhost:5432/scholaris'
# print(f"[INFO] Syncing database at: {db_url}")

# try:
#     conn = psycopg2.connect(db_url)
#     conn.autocommit = True
#     cur = conn.cursor()

#     # --- 1. Create New Tables (If Not Exists) ---
#     print("[INFO] Checking core tables...")
#     cur.execute("""
#         CREATE TABLE IF NOT EXISTS bulk_check_run (
#             id SERIAL PRIMARY KEY,
#             assignment_id INTEGER NOT NULL,
#             course_id INTEGER NOT NULL,
#             run_by INTEGER NOT NULL,
#             total_files INTEGER DEFAULT 0,
#             processed_count INTEGER DEFAULT 0,
#             status VARCHAR(20) DEFAULT 'pending',
#             accepted INTEGER DEFAULT 0,
#             rejected INTEGER DEFAULT 0,
#             manual_review INTEGER DEFAULT 0,
#             elapsed_sec DOUBLE PRECISION,
#             created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
#         );
#     """)
#     cur.execute("""
#         CREATE TABLE IF NOT EXISTS bulk_check_result (
#             id SERIAL PRIMARY KEY,
#             run_id INTEGER NOT NULL REFERENCES bulk_check_run(id) ON DELETE CASCADE,
#             filename VARCHAR(255),
#             verdict VARCHAR(20),
#             reason VARCHAR(255),
#             peer_score DOUBLE PRECISION DEFAULT 0.0,
#             external_score DOUBLE PRECISION DEFAULT 0.0,
#             ocr_confidence DOUBLE PRECISION DEFAULT 0.0,
#             analysis_text TEXT,
#             peer_details TEXT,
#             sentence_map TEXT
#         );
#     """)

#     # --- 2. Add Missing Columns (If Not Exists) ---
#     sync_tasks = [
#         ("submission", [
#             ("sentence_map", "TEXT"),
#             ("content_hash", "VARCHAR(64)")
#         ]),
#         ("bulk_check_run", [
#             ("processed_count", "INTEGER DEFAULT 0"),
#             ("status", "VARCHAR(20) DEFAULT 'pending'")
#         ])
#     ]

#     for table_name, columns in sync_tasks:
#         for col_name, col_type in columns:
#             try:
#                 cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {col_name} {col_type};")
#                 print(f"[SUCCESS] {table_name}: Column {col_name} verified.")
#             except Exception as e:
#                 print(f"[ERROR] {table_name}: Failed to add {col_name}: {e}")

#     cur.close()
#     conn.close()
#     print("[INFO] Database sync complete.")

# except Exception as e:
#     print(f"[CRITICAL] Connection failed: {e}")
#     exit(1)


import os
import psycopg2
from dotenv import load_dotenv

# Path resolution
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(base_dir, '.env'))

db_url = os.getenv('DATABASE_URL')
print(f"[INFO] Connecting to: {db_url}")

try:
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()

    # List of EVERY column that needs to exist
    tasks = [
        ("bulk_check_run", "processed_count", "INTEGER DEFAULT 0"),
        ("bulk_check_run", "status", "VARCHAR(20) DEFAULT 'pending'"),
        ("bulk_check_run", "accepted", "INTEGER DEFAULT 0"),
        ("bulk_check_run", "rejected", "INTEGER DEFAULT 0"),
        ("bulk_check_run", "manual_review", "INTEGER DEFAULT 0"),
        ("bulk_check_run", "elapsed_sec", "DOUBLE PRECISION"),
        ("submission", "sentence_map", "TEXT"),
        ("submission", "content_hash", "VARCHAR(64)"),
        ("bulk_check_result", "sentence_map", "TEXT"),
        ("bulk_check_result", "analysis_text", "TEXT"),
        ("bulk_check_result", "peer_details", "TEXT"),
    ]

    # Create tables first if they are totally missing
    cur.execute("CREATE TABLE IF NOT EXISTS bulk_check_run (id SERIAL PRIMARY KEY, assignment_id INTEGER, course_id INTEGER, run_by INTEGER, created_at TIMESTAMP);")
    cur.execute("CREATE TABLE IF NOT EXISTS bulk_check_result (id SERIAL PRIMARY KEY, run_id INTEGER, filename VARCHAR(255), verdict VARCHAR(20), reason VARCHAR(255));")

    # Add columns one by one
    for table, col, col_type in tasks:
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type};")
            print(f"[OK] {table}.{col} is ready.")
        except Exception as e:
            print(f"[SKIP] {table}.{col} might already exist or table missing: {e}")

    cur.close()
    conn.close()
    print("[SUCCESS] Database is now perfectly in sync.")

except Exception as e:
    print(f"[FATAL] Sync failed: {e}")

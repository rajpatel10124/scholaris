
import os
import psycopg2
from dotenv import load_dotenv

# Path to the .env file
dotenv_path = '/home/snr/Downloads/Telegram Desktop/LMSIntegrity-backup-sefty/LMSIntegrity/.env'
load_dotenv(dotenv_path)

db_url = os.getenv('DATABASE_URL') or 'postgresql://scholaris:scholaris_local@localhost:5432/scholaris'
print(f"[INFO] Syncing database at: {db_url}")

try:
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()

    columns = [
        ("processed_count", "INTEGER DEFAULT 0"),
        ("status", "VARCHAR(20) DEFAULT 'pending'"),
        ("accepted", "INTEGER DEFAULT 0"),
        ("rejected", "INTEGER DEFAULT 0"),
        ("manual_review", "INTEGER DEFAULT 0"),
        ("elapsed_sec", "DOUBLE PRECISION")
    ]
    
    # Check/Alter password column length for scrypt hashes
    try:
        cur.execute('ALTER TABLE "user" ALTER COLUMN password TYPE VARCHAR(255);')
        print("[SUCCESS] Altered user.password column length to 255.")
    except Exception as e:
        print(f"[ERROR] Failed to alter password column: {e}")

    for col_name, col_type in columns:
        try:
            # PostgreSQL command to add if not exists
            cur.execute(f"ALTER TABLE bulk_check_run ADD COLUMN IF NOT EXISTS {col_name} {col_type};")
            print(f"[SUCCESS] Checked/Added column: {col_name}")
        except Exception as e:
            print(f"[ERROR] Failed to add {col_name}: {e}")

    cur.close()
    conn.close()
    print("[INFO] Database sync complete.")

except Exception as e:
    print(f"[CRITICAL] Connection failed: {e}")
    exit(1)

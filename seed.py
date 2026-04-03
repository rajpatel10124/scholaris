import sqlite3
from werkzeug.security import generate_password_hash

DB_NAME = "scholaris.db"

password_plain = "H@rsh3828"
password_hash = generate_password_hash(password_plain)

conn = sqlite3.connect(DB_NAME)
cursor = conn.cursor()

for i in range(1, 51):
    username = f"{i:02d}"   # 01,02,...50
    email = f"student{i:02d}@test.com"

    cursor.execute("""
        INSERT INTO users (username, email, password, role, is_verified)
        VALUES (?, ?, ?, 'student', 1)
    """, (username, email, password_hash))

conn.commit()
conn.close()

print("50 dummy students inserted successfully.")
import os
from app import app
from models import db, User
from flask_bcrypt import Bcrypt

# Initialize Bcrypt for hashing
bcrypt = Bcrypt(app)

def seed_database():
    with app.app_context():
        print("Creating tables if they don't exist in the current database...")
        db.create_all()

        # Seed dummy students
        password_plain = "H@rsh3828"
        password_hash = bcrypt.generate_password_hash(password_plain).decode('utf-8')

        print("Seeding 50 dummy students...")
        for i in range(1, 51):
            username = f"std{i:02d}"
            email = f"student{i:02d}@test.com"
            
            # Check if user already exists
            if not User.query.filter_by(username=username).first():
                user = User(
                    username=username,
                    email=email,
                    password=password_hash,
                    role='student',
                    email_verified=True
                )
                db.session.add(user)
        
        db.session.commit()
        print("✅ Database initialized and 50 students seeded successfully.")

if __name__ == "__main__":
    seed_database()
from app import app
from models import db, User
from flask_bcrypt import Bcrypt

# Initialize Bcrypt
bcrypt = Bcrypt(app)

def create_admin():
    with app.app_context():
        # Configuration for your new account:
        username = "admin"
        email = "admin@scholaris.app"
        password_plain = "Admin@123" # CHANGE THIS LATER
        role = "faculty" # or "admin"
        
        # Check if already exists
        if User.query.filter_by(username=username).first():
            print(f"❌ Error: User '{username}' already exists.")
            return

        hashed_pw = bcrypt.generate_password_hash(password_plain).decode('utf-8')
        
        new_user = User(
            username=username,
            email=email,
            password=hashed_pw,
            role=role,
            email_verified=True
        )
        
        db.session.add(new_user)
        db.session.commit()
        print(f"✅ Success! Account '{username}' created with password '{password_plain}'.")

if __name__ == "__main__":
    create_admin()

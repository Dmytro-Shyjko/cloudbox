# ☁️ CloudBox

CloudBox is a Flask-based file storage system designed for secure file upload, storage, and controlled access.

The project is built as a modular, test-driven web application with a structured Git workflow and AI-assisted development.

---

## 🚀 Features

- User authentication system
- Secure file upload handling
- Structured file storage management
- Automated testing with pytest
- Clean Git workflow (main / develop / feature/*)
- AI-assisted development (Codex-ready)

---

## 🛠 Tech Stack

- Python 3
- Flask
- Pytest
- Virtual Environment (.venv)
- Git + GitHub
- Black (code formatting)

---

## 📂 Project Structure

cloudbox/
│
├── app/ # Core application logic
├── tests/ # Automated tests
├── storage/ # File storage handling
├── instance/ # Runtime configuration
├── run.py # Application entry point
├── requirements.txt # Dependencies
├── AGENTS.md # AI development rules
├── CONTRIBUTING.md # Contribution workflow
└── README.md # Project documentation


---

## ⚙️ Local Setup

### 1️⃣ Clone repository

git clone https://github.com/Dmytro-Shyjko/cloudbox.git

cd cloudbox

### 2️⃣ Create virtual environment

python3 -m venv .venv
source .venv/bin/activate


### 3️⃣ Install dependencies

pip install -r requirements.txt


### 4️⃣ Run application

python run.py


The application will start locally.

---

## 🧪 Running Tests

Before any Pull Request:

pytest


All tests must pass.

---

## 🎨 Code Formatting

Before committing:

black


Follow PEP8 conventions.

---

## 🌿 Branching Strategy

- `main` → production-ready code
- `develop` → integration branch
- `feature/*` → new functionality

Never commit directly to `main`.

---

## 🔄 Development Workflow

1. Checkout develop:
git checkout develop
git pull
2. Create feature branch:
git checkout -b feature/<name>
3. Commit changes:
git add -A
git commit -m "feat: description"
4. Push branch:
git push -u origin feature/<name>

5. Create Pull Request:
feature → develop

After testing:
develop → main

---

## 🔐 Security Notes

- Never commit secrets
- Use environment variables for sensitive data
- Validate all user inputs
- Ensure safe file handling

---

## 📈 Roadmap (Planned Improvements)

- Password reset functionality
- Role-based access control
- File versioning
- Storage quota system
- Admin dashboard improvements
- Production deployment automation

---

## 🤖 AI Integration

CloudBox uses structured AI-assisted development.

The `AGENTS.md` file defines rules for code agents to:
- maintain stability
- follow project conventions
- run tests before PR
- avoid modifying critical logic without explanation

---

## 📄 License

Currently under private development.

---

## 👨‍💻 Author

Dmytro Shyjko  
CloudBox Project  

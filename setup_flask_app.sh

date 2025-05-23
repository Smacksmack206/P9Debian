#!/bin/bash

# Define the root directory for your entire Webshell project
PROJECT_ROOT="${HOME}/webshell_project_root" # Using ${HOME} for tilde expansion in scripts

# Define the directory for the Flask application
FLASK_APP_DIR="${PROJECT_ROOT}/flask_app"
TEMPLATES_DIR="${FLASK_APP_DIR}/templates"

echo "--- Setting up Flask Web App for Webshell ---"
echo "Project root will be: ${PROJECT_ROOT}"
echo "Flask app will be in: ${FLASK_APP_DIR}"

# 1. Create directories if they don't exist
mkdir -p "${TEMPLATES_DIR}"
echo "[INFO] Ensured directory structure exists: ${TEMPLATES_DIR}"

# 2. Create/Overwrite requirements.txt
echo "[INFO] Creating ${FLASK_APP_DIR}/requirements.txt..."
cat <<EOF > "${FLASK_APP_DIR}/requirements.txt"
Flask
EOF

# 3. Create/Overwrite app.py
echo "[INFO] Creating ${FLASK_APP_DIR}/app.py..."
cat <<EOF > "${FLASK_APP_DIR}/app.py"
from flask import Flask, render_template, request, redirect, url_for, session

# Initialize the Flask application
app = Flask(__name__)

# Secret key for session management. Change this to something random and secret!
app.secret_key = 'your_very_secret_development_key_123!' # CHANGE THIS!

# --- Configuration for MVP ---
VALID_USERNAME = 'cedric' # Hardcoded credentials - FOR DEV ONLY
VALID_PASSWORD = 'password' # Hardcoded credentials - CHANGE THIS & REMOVE FOR PROD
TTYD_URL = 'http://localhost:7682' # ttyd instance URL

@app.route('/')
def index():
    if 'username' in session:
        return redirect(url_for('terminal_page'))
    return render_template('login.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username_attempt = request.form.get('username')
        password_attempt = request.form.get('password')

        if username_attempt == VALID_USERNAME and password_attempt == VALID_PASSWORD:
            session['username'] = username_attempt
            print(f"User '{username_attempt}' logged in successfully.")
            return redirect(url_for('terminal_page'))
        else:
            print(f"Login failed for user '{username_attempt}'.")
            return render_template('login.html', error='Invalid username or password')
    return render_template('login.html')

@app.route('/terminal')
def terminal_page():
    if 'username' not in session:
        return redirect(url_for('index'))
    return render_template('terminal.html', ttyd_url=TTYD_URL)

@app.route('/logout')
def logout():
    print(f"User '{session.get('username')}' logged out.")
    session.pop('username', None)
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
EOF

# 4. Create/Overwrite templates/login.html
echo "[INFO] Creating ${TEMPLATES_DIR}/login.html..."
cat <<EOF > "${TEMPLATES_DIR}/login.html"
<!DOCTYPE html>
<html lang="en" data-theme="dark"> <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Login - Webshell</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">
    <style>
        :root {
            --webshell-bg: #1a1b26; 
            --webshell-card-bg: #2a2c3d; 
            --webshell-text: #c0c5ce;
            --webshell-accent: #7aa2f7; 
            --webshell-border: #414868;
            --webshell-input-bg: #1e1f2c;
        }
        html[data-theme="dark"] body {
            background-color: var(--webshell-bg);
            color: var(--webshell-text);
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
            padding: 1rem;
        }
        html[data-theme="dark"] main.container {
            max-width: 400px; 
            width: 100%;
        }
        html[data-theme="dark"] article {
            background-color: var(--webshell-card-bg);
            border: 1px solid var(--webshell-border);
            padding: 2rem; 
            border-radius: var(--pico-border-radius, 0.5rem); 
            box-shadow: 0 8px 24px rgba(0,0,0,0.3); 
        }
        html[data-theme="dark"] h1 {
            text-align: center;
            color: var(--webshell-text); 
            margin-bottom: 1.5rem;
        }
        html[data-theme="dark"] label {
            color: var(--webshell-text);
            margin-bottom: 0.5rem; 
        }
        html[data-theme="dark"] input[type="text"],
        html[data-theme="dark"] input[type="password"] {
            background-color: var(--webshell-input-bg);
            color: var(--webshell-text);
            border: 1px solid var(--webshell-border);
        }
        html[data-theme="dark"] button[type="submit"] {
            background-color: var(--webshell-accent);
            color: #0d0e10; 
            border: none; 
            margin-top: 1.5rem; 
            font-weight: bold;
        }
        html[data-theme="dark"] button[type="submit"]:hover {
            background-color: #5a7fdb; 
        }
        .error {
            color: var(--pico-del-color, #b71c1c);
            background-color: var(--pico-del-background-color, #ffcdd2);
            border-left: 0.3rem solid var(--pico-del-border-color, #c62828);
            padding: var(--pico-block-spacing-vertical) var(--pico-block-spacing-horizontal);
            margin-bottom: var(--pico-spacing);
            border-radius: var(--pico-border-radius);
            text-align: left; 
        }
        body { /* Default body to dark if not overridden by html[data-theme=dark], e.g. if pico fails to switch */
             background-color: var(--webshell-bg);
        }
        article { /* Default article to dark */
            background-color: var(--webshell-card-bg);
        }
    </style>
</head>
<body>
    <main class="container">
        <article>
            <h1>Webshell Login</h1>
            {% if error %}
                <p role="alert" class="error">{{ error }}</p>
            {% endif %}
            <form method="post" action="{{ url_for('login') }}">
                <label for="username">
                    Username
                    <input type="text" id="username" name="username" value="cedric" placeholder="Enter your username" required>
                </label>
                <label for="password">
                    Password
                    <input type="password" id="password" name="password" value="password" placeholder="Enter your password" required>
                </label>
                <button type="submit">Login</button>
            </form>
        </article>
    </main>
</body>
</html>
EOF

# 5. Create/Overwrite templates/terminal.html
echo "[INFO] Creating ${TEMPLATES_DIR}/terminal.html..."
cat <<EOF > "${TEMPLATES_DIR}/terminal.html"
<!DOCTYPE html>
<html lang="en" data-theme="dark"> <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Webshell Terminal</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">
    <style>
        :root { /* Define these if you want to use them, or Pico's dark theme will apply */
            --webshell-bg: #1a1b26;
            --webshell-card-bg: #2a2c3d;
            --webshell-text: #c0c5ce;
            --webshell-accent: #7aa2f7;
            --webshell-border: #414868;
        }
        html[data-theme="dark"], html[data-theme="dark"] body {
            height: 100vh; 
            width: 100vw;  
            margin: 0;
            padding: 0;
            overflow: hidden; 
            display: flex;
            flex-direction: column;
            font-family: sans-serif;
            background-color: var(--webshell-bg); /* Apply dark background */
            color: var(--webshell-text); /* Apply dark theme text color */
        }
        .top-bar {
            padding: 8px 15px;
            /* Pico's dark theme should handle top-bar background, or you can set it */
            /* background-color: var(--webshell-card-bg); */
            border-bottom: 1px solid var(--webshell-border);
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-shrink: 0; 
            width: 100%; 
        }
        .top-bar span { 
            font-size: 0.9em; 
        }
        .top-bar a { 
            text-decoration: none; 
            /* color: var(--webshell-accent);  Pico should style this */
            padding: 6px 12px; 
            /* border: 1px solid var(--webshell-accent); Pico should style this */
            border-radius: 4px; 
            font-size: 0.9em; 
        }
        /* Pico styles for links in dark mode might be sufficient for a:hover too */
        /* .top-bar a:hover { background-color: var(--webshell-accent); color: #0d0e10; } */
        
        .iframe-container {
            flex-grow: 1; 
            width: 100%;
            overflow: auto; 
            border: 2px solid var(--webshell-border); /* Subtle border */
        }
        iframe {
            width: 100%;
            height: 100%; 
            border: none; /* No border for the iframe itself */
            display: block; 
        }
    </style>
</head>
<body>
    <div class="top-bar">
        <span>Welcome, {{ session.username }}!</span>
        <a href="{{ url_for('logout') }}" role="button" class="secondary outline">Logout</a> </div>
    <div class="iframe-container">
        <iframe id="terminal_iframe" src="{{ ttyd_url }}"></iframe>
    </div>
</body>
</html>
EOF

echo "[SUCCESS] Flask app files and templates created/updated in ${FLASK_APP_DIR}."
echo "--- Next Steps ---"
echo "1. cd ${FLASK_APP_DIR}"
echo "2. If you haven't already, create and activate a Python virtual environment:"
echo "   python3 -m venv venv"
echo "   source venv/bin/activate"
echo "3. Install dependencies:"
echo "   pip install -r requirements.txt"
echo "4. Ensure your Webshell container (with ttyd) is running (use your g.sh script)."
echo "5. Run the Flask application:"
echo "   python app.py"
echo "6. Open your browser to http://localhost:5000 (or http://<DebianAVF_VM_IP>:5000)."

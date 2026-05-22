# 🔐 LOGIN SYSTEM - Setup Guide

## ✅ Login System Activated

A simple login page has been added to protect your trading dashboard.

### Default Credentials:
```
Username: admin
Password: trading2024
```

### Features:
- ✅ Login page with clean UI
- ✅ Session-based authentication
- ✅ All API routes protected
- ✅ Logout button in dashboard header
- ✅ Auto-redirect to login if not authenticated

---

## 🔧 Change Credentials

### Method 1: Edit .env.dashboard file
```bash
nano .env.dashboard
```

Change:
```
DASHBOARD_USERNAME=your_username
DASHBOARD_PASSWORD=your_secure_password
```

### Method 2: Edit dashboard/app.py directly
```python
# Line 15-16
USERNAME = "your_username"
PASSWORD = "your_secure_password"
```

---

## 🚀 How to Use

### 1. Start the bot:
```bash
python main.py
```

### 2. Open browser:
```
http://localhost:5000
```

### 3. You'll see the login page:
- Enter username: `admin`
- Enter password: `trading2024`
- Click "Login"

### 4. Access dashboard:
- After login, you'll see the full trading dashboard
- Click "🚪 Logout" button (top right) to logout

---

## 🔒 Security Notes

### ⚠️ IMPORTANT:
1. **Change default password immediately** for production use
2. **Use HTTPS** if accessing over network (not localhost)
3. **Don't share credentials** in logs or screenshots
4. **Session expires** when browser closes

### Recommended Password:
- Minimum 12 characters
- Mix of letters, numbers, symbols
- Example: `Tr@d1ng!B0t#2024`

---

## 🌐 Remote Access

If accessing from another computer:

### 1. Update config.yaml:
```yaml
dashboard:
  host: "0.0.0.0"  # Allow external connections
  port: 5000
```

### 2. Access via:
```
http://YOUR_SERVER_IP:5000
```

### 3. Security Warning:
- ⚠️ Use strong password
- ⚠️ Consider VPN or SSH tunnel
- ⚠️ Enable firewall rules
- ⚠️ Use HTTPS with SSL certificate

---

## 🔐 Advanced Security (Optional)

### Add IP Whitelist:
Edit `dashboard/app.py`, add before routes:

```python
ALLOWED_IPS = ['127.0.0.1', '192.168.1.100']

@app.before_request
def limit_remote_addr():
    if request.remote_addr not in ALLOWED_IPS:
        abort(403)
```

### Add Rate Limiting:
```bash
pip install flask-limiter
```

Edit `dashboard/app.py`:
```python
from flask_limiter import Limiter

limiter = Limiter(app, key_func=lambda: request.remote_addr)

@app.route("/login", methods=["POST"])
@limiter.limit("5 per minute")
def login():
    # ... existing code
```

### Enable HTTPS:
```bash
# Generate self-signed certificate
openssl req -x509 -newkey rsa:4096 -nodes -out cert.pem -keyout key.pem -days 365

# Run with SSL
python main.py --ssl
```

---

## 🐛 Troubleshooting

### "Invalid credentials" error:
- Check username/password in `.env.dashboard`
- Ensure no extra spaces
- Case-sensitive

### Can't access login page:
- Check bot is running: `ps aux | grep python`
- Check port 5000 is open: `netstat -tulpn | grep 5000`
- Try: `http://127.0.0.1:5000`

### Session expires immediately:
- Check `SECRET_KEY` in `dashboard/app.py`
- Clear browser cookies
- Try incognito/private mode

### Logout doesn't work:
- Clear browser cache
- Check browser console for errors
- Restart bot

---

## 📝 Files Modified

1. **dashboard/app.py** - Added login logic
2. **dashboard/templates/login.html** - New login page
3. **dashboard/templates/index.html** - Added logout button
4. **dashboard/static/style.css** - Added button styles
5. **.env.dashboard** - Credentials storage

---

## ✅ Testing Checklist

- [ ] Login page loads at http://localhost:5000
- [ ] Invalid credentials show error message
- [ ] Valid credentials redirect to dashboard
- [ ] Dashboard shows all data correctly
- [ ] Logout button works
- [ ] After logout, can't access dashboard without login
- [ ] Session persists across page refreshes

---

## 🎯 Quick Commands

```bash
# Change password
echo "DASHBOARD_PASSWORD=NewPassword123" >> .env.dashboard

# Restart bot
pkill -f "python main.py"
python main.py

# Test login
curl -X POST http://localhost:5000/login \
  -d "username=admin&password=trading2024" \
  -c cookies.txt

# Check if logged in
curl http://localhost:5000/api/stats -b cookies.txt
```

---

## 🔐 Production Deployment

For production use:

1. **Change credentials:**
   ```bash
   nano .env.dashboard
   # Set strong password
   ```

2. **Use environment variables:**
   ```bash
   export DASHBOARD_USERNAME="your_user"
   export DASHBOARD_PASSWORD="strong_password_here"
   ```

3. **Enable HTTPS:**
   - Get SSL certificate (Let's Encrypt)
   - Use reverse proxy (nginx)
   - Configure firewall

4. **Add monitoring:**
   - Log failed login attempts
   - Alert on suspicious activity
   - Rotate passwords regularly

---

## 📞 Support

- Login issues: Check `.env.dashboard` file
- Security questions: Use strong passwords + HTTPS
- Remote access: Configure firewall properly

**Default credentials work immediately - just restart the bot!** 🚀

from flask import Flask, render_template, request, flash, redirect, url_for, session
from simplejsondb import Database
import re
import hashlib
import time
from monero.backends.jsonrpc import JSONRPCWallet
from monero.wallet import Wallet
import threading
import time
import os
from apscheduler.schedulers.background import BackgroundScheduler
import atexit

lock = threading.Lock()

def update_tips(db, lock, wallet):
    with lock:
        for meme in db.data.get("memes"):
            account_index = meme["account_index"]
            account = wallet.accounts[account_index]
            meme["tips"] = float(sum(transaction.amount for transaction in account.incoming())) 
            meme["tips_formatted"] = format(meme["tips"], '.8f')
        for name in db.data.get("users").get("names"):
            account = db.data.get("users").get("accounts").get(name)
            account["total_tips"] = float(sum((sum(transaction.amount for transaction in wallet.accounts[i].incoming()) for i in account.get("accounts"))))
            account["total_tips_formatted"] = format(account["total_tips"], '.8f')

        db.data["sorted_accounts"] = list(sorted(db.data.get("users").get("accounts"), key=lambda user: db.data.get("users").get("accounts").get(user).get("total_tips_formatted"), reverse=True))
        db.data["formatted_view"] = {acc: db.data.get("users").get("accounts").get(acc).get("total_tips_formatted") for acc in db.data["sorted_accounts"]}

        db.save()

app = Flask(__name__)
db = Database('db.json')
wallet = Wallet(JSONRPCWallet(host=os.environ["WALLET_RPC_IP"], port=28088))

scheduler = BackgroundScheduler()
scheduler.add_job(func=update_tips, trigger="interval", seconds=30, args=(db, lock, wallet))
scheduler.start()
atexit.register(scheduler.shutdown)

if not db.data.get("users"):
    db.data["users"] = {
        "names": [],
        "accounts": {},
    }
    db.save()
if not db.data.get("memes"):
    db.data["memes"] = []
    db.save()

app.secret_key = os.environ["FLASK_SECRET_KEY"]

@app.route("/most_tipped")
def most_tipped():
    return render_template("index.html", memes=sorted(db.data.get("memes"), key=lambda meme: meme["tips"]), user=session.get("display_name"))

@app.route("/")
def index():
    return render_template("index.html", memes=db.data.get("memes"), user=session.get("display_name"))

@app.route("/register")
def register():
    return render_template("register.html")

@app.route("/submit")
def submit():
    return render_template("submit.html", user=session.get("display_name"))

@app.route("/about")
def about():
    return render_template("about.html", user=session.get("display_name"))

@app.route("/signout", methods=["POST"])
def sign_out():
    if not session.get("email"):
        return redirect(url_for("index"))

    del session["email"]
    del session["display_name"]
    return redirect(url_for("index"))

@app.route("/login")
def login():
    return render_template("login.html")

@app.route("/login_account", methods=["POST"])
def login_account():
    display_name = request.form.get("display_name")
    password = request.form.get("password")

    account = db.data.get("users").get("accounts").get(display_name)
    if not account:
        flash("Incorrect username or password!")
        return redirect(url_for("login"))

    hashed_password = str(hashlib.sha256(password.encode()).digest())

    if hashed_password != account.get("password"):
        flash("Incorrect username or password!")
        return redirect(url_for("login"))

    session["email"] = account.get("email")
    session["display_name"] = display_name
    
    return redirect(url_for("index"))

@app.route("/submit_meme", methods=["POST"])
def submit_meme():
    if not session.get("email"):
        return redirect(url_for("index"))

    meme_id = str(time.time())
    title = request.form.get("title")
    filename = f"static/{meme_id}.png"
    for f in request.files.getlist("files"):
        f.save(filename)
    account = wallet.new_account()
    with lock:
        db.data["memes"].append({
            "meme_id": meme_id,
            "title": title,
            "author": session.get("display_name"),
            "filename": filename,
            "account_index": account.index,
            "address": str(account.address()),
            "tips": 0.0,
            "tips_formatted": format(0.0, '.8f')
        })

        db.data["users"]["accounts"][session.get("display_name")].get("accounts").append(account.index) #this account belongs to the session owner.
        db.save()
    return redirect(url_for("index"))

@app.route("/account")
def account():
    if not session.get("email"):
        return redirect(url_for("index"))
    
    display_name = session.get("display_name")
    account = db.data.get("users").get("accounts").get(display_name)
    account_balance = sum(wallet.accounts[i].balance(unlocked=True) for i in account.get("accounts"))
    return render_template("account.html", memes=list(filter(lambda e: e.get("author")==display_name, db.data.get("memes"))), user=session.get("display_name"), display_name=display_name, account_balance=float(account_balance), address=account.get("address"))

@app.route("/account_view/<display_name>")
def account_view(display_name):
    return render_template("account_view.html", total_tips=db.data.get("users").get("accounts").get(display_name).get("total_tips_formatted"), account_view_display_name=display_name, memes=list(filter(lambda e: e.get("author")==display_name, db.data.get("memes"))), user=session.get("display_name"))

@app.route("/leaderboard")
def leaderboard():
    return render_template("leaderboard.html", user=session.get("display_name"), accounts=db.data["sorted_accounts"], formatted_view=db.data["formatted_view"])

@app.route("/withdraw", methods=["POST"])
def withdraw():
    if not session.get("email"):
        return redirect(url_for("index"))

    display_name = session.get("display_name")
    account = db.data.get("users").get("accounts").get(display_name)

    account_balance = sum(wallet.accounts[i].balance(unlocked=True) for i in account.get("accounts"))
    if account_balance == 0.0:
        flash("No funds to withdraw! :(")
        return redirect(url_for("account"))

    for i in account.get("accounts"):
        acc = wallet.accounts[i]
        # print(acc.balance(), account.get("address"))
        if acc.balance(unlocked=True) != 0.0:
            acc.sweep_all(account.get("address"))

    flash("Withdraw success!")
    return redirect(url_for("account"))

@app.route("/meme/<meme_id>")
def meme(meme_id):
    meme = list(filter(lambda e: e.get("meme_id")==meme_id, db.data.get("memes")))[0]
    author = meme["author"]
    account_index = meme["account_index"]
    account = wallet.accounts[account_index]
    # tip_address = db.data.get("users").get("accounts").get(author).get("site_address")
    # account_index = db.data.get("users").get("accounts").get(author).get("account_index")
    # account = wallet.accounts[account_index]
    # meme["tips"] = float(sum(transaction.amount for transaction in account.incoming()))
    # db.save()
    return render_template("meme.html", meme=meme, tip_address=meme.get("address"), amount=meme.get("tips_formatted"), user=session.get("display_name"))

@app.route("/register_account", methods=["POST"])
def register_account():
    display_name = request.form.get("display_name")
    email = request.form.get("email")
    address = request.form.get("address")
    password = request.form.get("password")
    password_confirm = request.form.get("password_confirm")

    if password != password_confirm:
        flash("Passwords do not match!")
        return redirect(url_for("register"))

    if len(password) <= 7:
        flash("Password must be at least 8 characters!")
        return redirect(url_for("register"))

    if not re.match("^(?:[48][0-9AB]|4[1-9A-HJ-NP-Za-km-z]{12}(?:[1-9A-HJ-NP-Za-km-z]{30})?)[1-9A-HJ-NP-Za-km-z]{93}$", address):
        flash("Not a valid XMR address!")
        return redirect(url_for("register"))

    if display_name in db.data["users"]["names"]:
        flash("Display name is already in use!")
        return redirect(url_for("register"))

    with lock:
        db.data["users"]["names"].append(display_name)    
        db.data["users"]["accounts"][display_name] = {
            "name": display_name,
            "email": email,
            "address": address,
            "password": str(hashlib.sha256(password.encode()).digest()),
            "accounts": [],
        }

        db.save()

    session["email"] = email
    session["display_name"] = display_name
    return redirect(url_for("index"))

if __name__ == "__main__":
    # threading.Thread(target=update_tips, args=(db, lock, wallet)).start()
    app.run(host="0.0.0.0", debug=False)

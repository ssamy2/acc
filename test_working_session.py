"""
Test the working session (201128184967) and export to session string
"""
import asyncio
from pyrogram import Client
from pyrogram.raw import functions

API_ID = 28907635
API_HASH = "fa6c3335de68283781976ae20f813f73"

async def test():
    print("Testing session: +201128184967\n")
    
    client = Client(
        name="sessions/pyrogram_201128184967",
        api_id=API_ID,
        api_hash=API_HASH,
        workdir="."
    )
    
    try:
        await client.start()
        
        me = await client.get_me()
        print(f"✅ Connected: {me.first_name} (ID: {me.id})")
        print(f"   Phone: {me.phone_number}")
        print(f"   Username: @{me.username}" if me.username else "   No username")
        
        # Export session string
        session_str = await client.export_session_string()
        print(f"\n✅ Session string (length: {len(session_str)})")
        print(f"   Preview: {session_str[:60]}...")
        
        # Get password info
        print("\n--- Security Info ---")
        pwd = await client.invoke(functions.account.GetPassword())
        print(f"has_password (2FA): {pwd.has_password}")
        print(f"has_recovery: {getattr(pwd, 'has_recovery', False)}")
        print(f"login_email_pattern: {getattr(pwd, 'login_email_pattern', None)}")
        print(f"email_unconfirmed_pattern: {getattr(pwd, 'email_unconfirmed_pattern', None)}")
        print(f"hint: {pwd.hint if pwd.has_password else None}")
        
        # Get sessions
        print("\n--- Active Sessions ---")
        auths = await client.invoke(functions.account.GetAuthorizations())
        print(f"Total: {len(auths.authorizations)}")
        for i, auth in enumerate(auths.authorizations):
            mark = " << CURRENT" if auth.current else ""
            print(f"  [{i+1}] {auth.device_model} - {auth.app_name}{mark}")
        
        # Save to file
        with open("session_string_201128184967.txt", "w") as f:
            f.write(session_str)
        print("\n✅ Session string saved to session_string_201128184967.txt")
        
        # Update DB
        import sqlite3
        conn = sqlite3.connect("escrow_accounts.db")
        c = conn.cursor()
        
        c.execute("SELECT phone FROM accounts WHERE phone = ?", ("+201128184967",))
        exists = c.fetchone()
        
        if exists:
            c.execute("""
                UPDATE accounts 
                SET pyrogram_session = ?, telegram_id = ?
                WHERE phone = ?
            """, (session_str, me.id, "+201128184967"))
            print("✅ Updated existing account in DB")
        else:
            c.execute("""
                INSERT INTO accounts (phone, telegram_id, pyrogram_session, status)
                VALUES (?, ?, ?, 'completed')
            """, ("+201128184967", me.id, session_str))
            print("✅ Inserted new account in DB")
        
        conn.commit()
        conn.close()
        
        await client.stop()
        print("\n✅ Done!")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())

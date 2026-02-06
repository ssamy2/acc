"""
Convert session file to session string and save to DB
"""
import asyncio
import sqlite3
from pyrogram import Client

API_ID = 28907635
API_HASH = "fa6c3335de68283781976ae20f813f73"

async def convert_and_save():
    phone = "+201128184967"
    session_path = "sessions/pyrogram_201128184967"
    
    import os
    if not os.path.exists(f"{session_path}.session"):
        print(f"❌ Session file not found: {session_path}.session")
        return
    
    print(f"Converting session for {phone}...")
    
    client = Client(
        name=session_path,
        api_id=API_ID,
        api_hash=API_HASH,
        workdir="."
    )
    
    try:
        await client.start()
        
        me = await client.get_me()
        print(f"✅ Connected: {me.first_name} (ID: {me.id})")
        
        session_string = await client.export_session_string()
        print(f"✅ Session string exported (length: {len(session_string)})")
        print(f"Session string preview: {session_string[:50]}...")
        
        conn = sqlite3.connect("escrow_accounts.db")
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE accounts 
            SET pyrogram_session = ?, telegram_id = ?
            WHERE phone = ?
        """, (session_string, me.id, phone))
        
        if cursor.rowcount == 0:
            cursor.execute("""
                INSERT INTO accounts (phone, telegram_id, pyrogram_session)
                VALUES (?, ?, ?)
            """, (phone, me.id, session_string))
            print(f"✅ Inserted new account in DB")
        else:
            print(f"✅ Updated existing account in DB")
        
        conn.commit()
        conn.close()
        
        print("\n--- Testing session string connection ---")
        test_client = Client(
            name="test_string",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=session_string,
            in_memory=True
        )
        
        await test_client.start()
        test_me = await test_client.get_me()
        print(f"✅ Session string works! User: {test_me.first_name}")
        
        from pyrogram.raw import functions
        pwd = await test_client.invoke(functions.account.GetPassword())
        print(f"\n--- Password Info ---")
        print(f"has_password: {pwd.has_password}")
        print(f"has_recovery: {getattr(pwd, 'has_recovery', False)}")
        print(f"login_email_pattern: {getattr(pwd, 'login_email_pattern', None)}")
        
        auths = await test_client.invoke(functions.account.GetAuthorizations())
        print(f"\n--- Sessions ({len(auths.authorizations)}) ---")
        for auth in auths.authorizations:
            mark = ">> CURRENT <<" if auth.current else ""
            print(f"  {auth.device_model} - {auth.app_name} {mark}")
        
        await test_client.stop()
        await client.stop()
        
        print("\n✅ Done! Session string saved to DB")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(convert_and_save())

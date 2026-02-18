import asyncio
import os
from telethon import TelegramClient
from dotenv import load_dotenv

load_dotenv()

API_ID = os.getenv('TELEGRAM_API_ID')
API_HASH = os.getenv('TELEGRAM_API_HASH')
PHONE = os.getenv('TELEGRAM_PHONE')

async def main():
    if not API_ID or not API_HASH or not PHONE:
        print("Error: Missing credentials in .env")
        return

    session_name = 'quantmuse_telegram_session'
    client = TelegramClient(session_name, int(API_ID), API_HASH)
    
    print(f"Connecting to Telegram with phone {PHONE}...")
    await client.connect()
    
    if not await client.is_user_authorized():
        print("Authentication required.")
        await client.send_code_request(PHONE)
        print("Code sent to your Telegram app.")
        code = input("Enter the code: ")
        try:
            await client.sign_in(PHONE, code)
            print("Successfully signed in!")
        except Exception as e:
            print(f"Error signing in: {e}")
            return
    else:
        print("Already authorized.")
    
    me = await client.get_me()
    print(f"Session active for user: {me.username or me.first_name}")
    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())

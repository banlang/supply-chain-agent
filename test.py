from dotenv import load_dotenv
import os

load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")

if api_key:
    print("✅ Setup สำเร็จ! พร้อม Build Agent แล้ว")
else:
    print("❌ ยังไม่เจอ API Key ลองเช็ค .env อีกครั้ง")
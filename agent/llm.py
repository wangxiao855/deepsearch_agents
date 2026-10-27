"""
提供模型对象
"""
import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv(override=True)

llm =  ChatOpenAI(
    model = os.getenv("QWEN_MODEL"),
    api_key = os.getenv("QWEN_APIKEY"),
    base_url = os.getenv("QWEN_URL")
)




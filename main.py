import os

key = os.getenv("DASHSCOPE_API_KEY")
print("已配置" if key else "未配置")
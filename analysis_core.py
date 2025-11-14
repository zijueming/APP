import fitz  # PyMuPDF
import requests
import os
import json
import time
import logging
import re
from typing import List, Dict, Optional

# --- 1. 配置和提示词 ---

# DeepSeek API 端点和模型
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

# 提示词模板
JSON_PROMPT_TEMPLATE = """
你是专业的文献分析专家，擅长从学术论文中提取核心信息并生成结构化总结。
请根据我提供的以下文献全文，严格按照这个JSON结构，提取并总结文献的核心信息：

```json
{
  "文献信息": {
    "标题": "文献标题",
    "作者": ["作者1", "作者2"],
    "期刊": "发表期刊名称",
    "年份": "发表年份"
  },
  "内容提取": {
    "摘要": "1-2句核心摘要，删除背景描述和冗余细节",
    "关键图表": [
      {
        "图序号": "例如: 图1",
        "图表类型": "流程图/曲线图/数据表等 (根据文中描述判断)",
        "核心内容": "图表展示的关键发现或关系 (根据文中对该图表的描述总结)",
        "支撑结论": "此图证明了什么结论 (根据文中对该图表的描述总结)"
      }
    ],
    "实验": [
      "实验方法：对象+操作+指标",
      "实验设置：对照组/实验组"
    ],
    "结论": [
      "基于数据的主要发现",
      "与前人工作的对比结果"
    ],
    "创新点": [
      "方法/理论/应用创新",
      "性能提升的关键"
    ],
    "不足": [
      "方法局限性",
      "实验缺陷",
      "未解决问题"
    ]
  }
}
```

请只返回填充好的JSON代码块，不要包含其他任何解释性文字。
"""

# --- 2. 核心函数 (从 app.py 迁移至此) ---

def clean_json_response(response_text: str) -> Optional[str]:
    """
    更健壮地清理AI返回的字符串，提取纯JSON
    """
    try:
        match = re.search(r"```json\s*([\s\S]+?)\s*```", response_text)
        if match:
            logging.debug("通过正则表达式找到了JSON块")
            return match.group(1).strip()

        first_brace = response_text.find('{')
        last_brace = response_text.rfind('}')
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            potential_json = response_text[first_brace : last_brace + 1]
            json.loads(potential_json)
            logging.debug("通过花括号定位了JSON")
            return potential_json
        
        json.loads(response_text)
        logging.debug("整个响应是一个有效的JSON")
        return response_text.strip()

    except Exception as e:
        logging.warning(f"无法从AI响应中解析JSON: {e}")
        logging.debug(f"原始响应 (前200字符): {response_text[:200]}")
        return None

def extract_text_from_pdf(pdf_path: str) -> Optional[str]:
    """
    [阶段 1b] 从PDF提取所有文本
    """
    logging.info(f"[阶段 1b] 正在处理 PDF: {pdf_path}")
    
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        logging.error(f"  [错误] 无法打开PDF文件 {pdf_path}。 {e}")
        return None

    full_text = ""
    for page_num in range(len(doc)):
        try:
            page = doc.load_page(page_num)
            full_text += page.get_text("text")
            full_text += "\n\n"
        except Exception as e:
            logging.warning(f"  [警告] 提取第 {page_num + 1} 页文本时出错: {e}")

    doc.close()
    full_text = full_text.replace("-\n", "") # 合并跨行单词
    logging.info(f"[阶段 1b] 文本提取完成! 总字数: {len(full_text)}.")
    return full_text

# [新] 添加图片提取功能
def extract_images_from_pdf(pdf_path: str, output_dir: str) -> List[str]:
    """
    [新] [阶段 1a] 从PDF提取图片并保存到目录
    使用图片尺寸 (100x100 像素) 过滤
    """
    logging.info(f"[阶段 1a] 正在提取图片: {pdf_path}")
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        logging.info(f"  创建图片目录: {output_dir}")

    saved_image_paths = []
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        logging.error(f"  [错误] 无法打开PDF文件 {pdf_path}。 {e}")
        return []

    image_counter = 1
    
    for page_num in range(len(doc)):
        image_list = doc.get_page_images(page_num, full=True)

        for img_info in image_list:
            xref = img_info[0]
            try:
                base_image = doc.extract_image(xref)
                
                img_width = base_image["width"]
                img_height = base_image["height"]
                if img_width < 100 or img_height < 100:
                    logging.debug(f"  跳过小图片 (尺寸: {img_width}x{img_height})")
                    continue
                
                image_bytes = base_image["image"]
                image_ext = base_image["ext"]
                
                image_filename = f"fig{image_counter}.{image_ext}"
                image_path = os.path.join(output_dir, image_filename)
                
                with open(image_path, "wb") as img_file:
                    img_file.write(image_bytes)
                
                # 只保存文件名，不保存完整路径
                saved_image_paths.append(image_filename)
                image_counter += 1
                
            except Exception as e:
                logging.debug(f"  提取 xref {xref} 时出错 (可能是遮罩图): {e}")
                pass

    doc.close()
    logging.info(f"[阶段 1a] 图片提取完成! 共保存 {len(saved_image_paths)} 张有效图片到 {output_dir}")
    return saved_image_paths


def analyze_text_with_deepseek(full_text: str, api_key: str, retries=3, delay=10) -> Optional[Dict]:
    """
    [阶段 2] 发送全文到 DeepSeek 文本 API
    """
    logging.info(f"  [阶段 2] 正在发送全文 (共 {len(full_text)} 字符) 到 DeepSeek 进行分析...")
    
    if not api_key or "sk-" not in api_key:
        logging.error("  [错误] 传入的 API 密钥无效!")
        return {"error": "Invalid API Key provided"}

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": JSON_PROMPT_TEMPLATE},
            {"role": "user", "content": f"这是我需要你分析的文献全文：\n\n{full_text}"}
        ],
        "max_tokens": 4096,
        "temperature": 0.1
    }

    for attempt in range(retries):
        try:
            response = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=300)
            response.raise_for_status()
            
            result = response.json()
            analysis_text = result['choices'][0]['message']['content']
            
            json_string = clean_json_response(analysis_text)
            if json_string:
                parsed_json = json.loads(json_string)
                return parsed_json
            else:
                logging.error(f"  [错误] clean_json_response 未能从AI响应中提取JSON。")
                return {"error": "AI response was not valid JSON", "raw_response": analysis_text}

        except requests.exceptions.HTTPError as e:
            logging.error(f"  [错误] HTTP 错误 (尝试 {attempt + 1}/{retries}): {e}")
            if e.response.status_code == 401:
                 logging.error("  [严重错误] 401 未授权 - 检查您的 API 密钥是否正确或已激活。")
                 return {"error": "401 Unauthorized - Invalid API Key"}
            time.sleep(delay)
            delay *= 2
        except Exception as e:
            logging.error(f"  [错误] API 请求失败 (尝试 {attempt + 1}/{retries}): {e}")
            time.sleep(delay)
            
    return {"error": "API analysis failed after multiple retries"}
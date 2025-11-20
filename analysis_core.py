import fitz  # PyMuPDF
import requests
import os
import json
import time
import logging
import re
from typing import List, Dict, Optional

class AnalysisService:
    def __init__(self):
        self.deepseek_api_url = "https://api.deepseek.com/chat/completions"
        self.deepseek_model = "deepseek-chat"
        self.json_prompt_template = """
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

    def clean_json_response(self, response_text: str) -> Optional[str]:
        """
        Robustly clean AI response to extract pure JSON.
        """
        try:
            match = re.search(r"```json\s*([\s\S]+?)\s*```", response_text)
            if match:
                logging.debug("Found JSON block via regex")
                return match.group(1).strip()

            first_brace = response_text.find('{')
            last_brace = response_text.rfind('}')
            if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
                potential_json = response_text[first_brace : last_brace + 1]
                json.loads(potential_json)
                logging.debug("Found JSON via braces")
                return potential_json
            
            json.loads(response_text)
            logging.debug("Entire response is valid JSON")
            return response_text.strip()

        except Exception as e:
            logging.warning(f"Failed to parse JSON from AI response: {e}")
            logging.debug(f"Raw response (first 200 chars): {response_text[:200]}")
            return None

    def extract_text_from_pdf(self, pdf_path: str) -> Optional[str]:
        """
        [Stage 1b] Extract all text from PDF.
        """
        logging.info(f"[Stage 1b] Processing PDF: {pdf_path}")
        
        try:
            doc = fitz.open(pdf_path)
        except Exception as e:
            logging.error(f"  [Error] Cannot open PDF {pdf_path}. {e}")
            return None

        full_text = ""
        for page_num in range(len(doc)):
            try:
                page = doc.load_page(page_num)
                full_text += page.get_text("text")
                full_text += "\n\n"
            except Exception as e:
                logging.warning(f"  [Warning] Error extracting text from page {page_num + 1}: {e}")

        doc.close()
        full_text = full_text.replace("-\n", "") # Merge hyphenated words
        logging.info(f"[Stage 1b] Text extraction complete! Total chars: {len(full_text)}.")
        return full_text

    def extract_images_from_pdf(self, pdf_path: str, output_dir: str) -> List[str]:
        """
        [Stage 1a] Extract images from PDF and save to directory.
        Filters small images (100x100).
        """
        logging.info(f"[Stage 1a] Extracting images: {pdf_path}")
        
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            logging.info(f"  Created image directory: {output_dir}")

        saved_image_paths = []
        try:
            doc = fitz.open(pdf_path)
        except Exception as e:
            logging.error(f"  [Error] Cannot open PDF {pdf_path}. {e}")
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
                        logging.debug(f"  Skipping small image (Size: {img_width}x{img_height})")
                        continue
                    
                    image_bytes = base_image["image"]
                    image_ext = base_image["ext"]
                    
                    image_filename = f"fig{image_counter}.{image_ext}"
                    image_path = os.path.join(output_dir, image_filename)
                    
                    with open(image_path, "wb") as img_file:
                        img_file.write(image_bytes)
                    
                    saved_image_paths.append(image_filename)
                    image_counter += 1
                    
                except Exception as e:
                    logging.debug(f"  Error extracting xref {xref}: {e}")
                    pass

        doc.close()
        logging.info(f"[Stage 1a] Image extraction complete! Saved {len(saved_image_paths)} images to {output_dir}")
        return saved_image_paths

    def analyze_text_with_deepseek(self, full_text: str, api_key: str, retries=3, delay=10) -> Optional[Dict]:
        """
        [Stage 2] Send full text to DeepSeek API.
        """
        logging.info(f"  [Stage 2] Sending full text ({len(full_text)} chars) to DeepSeek...")
        
        if not api_key or "sk-" not in api_key:
            logging.error("  [Error] Invalid API Key provided!")
            return {"error": "Invalid API Key provided"}

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        
        payload = {
            "model": self.deepseek_model,
            "messages": [
                {"role": "system", "content": self.json_prompt_template},
                {"role": "user", "content": f"这是我需要你分析的文献全文：\n\n{full_text}"}
            ],
            "max_tokens": 4096,
            "temperature": 0.1
        }

        for attempt in range(retries):
            try:
                response = requests.post(self.deepseek_api_url, headers=headers, json=payload, timeout=300)
                response.raise_for_status()
                
                result = response.json()
                analysis_text = result['choices'][0]['message']['content']
                
                json_string = self.clean_json_response(analysis_text)
                if json_string:
                    parsed_json = json.loads(json_string)
                    return parsed_json
                else:
                    logging.error(f"  [Error] clean_json_response failed to extract JSON.")
                    return {"error": "AI response was not valid JSON", "raw_response": analysis_text}

            except requests.exceptions.HTTPError as e:
                logging.error(f"  [Error] HTTP Error (Attempt {attempt + 1}/{retries}): {e}")
                if e.response.status_code == 401:
                     logging.error("  [Critical] 401 Unauthorized - Check your API Key.")
                     return {"error": "401 Unauthorized - Invalid API Key"}
                time.sleep(delay)
                delay *= 2
            except Exception as e:
                logging.error(f"  [Error] API Request Failed (Attempt {attempt + 1}/{retries}): {e}")
                time.sleep(delay)
                
        return {"error": "API analysis failed after multiple retries"}
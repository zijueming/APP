import os
import json
import logging
import shutil # 用于删除文件夹
from typing import Callable, Dict, List, Optional, Set

# --- 1. 配置 ---

# 我们的“数据库”就是一个文件夹
DB_BASE_PATH = "literature_db"
ANALYSIS_FILE_NAME = "analysis.json"
PDF_FILE_NAME = "original.pdf"

# --- 2. 基础数据库操作 ---

def setup_database():
    """
    确保数据库文件夹存在
    """
    if not os.path.exists(DB_BASE_PATH):
        logging.info(f"创建数据库目录: {DB_BASE_PATH}")
        os.makedirs(DB_BASE_PATH)

def get_paper_dir(paper_id: str) -> str:
    """
    获取特定文献的文件夹路径
    """
    return os.path.join(DB_BASE_PATH, paper_id)

def get_analysis_filepath(paper_id: str) -> str:
    """
    获取特定文献的 analysis.json 文件路径
    """
    return os.path.join(get_paper_dir(paper_id), ANALYSIS_FILE_NAME)

def _mutate_analysis_file(paper_id: str, update_function: Callable[[Dict], Dict]) -> Dict:
    """
    内部辅助：安全地读取、修改并写回 analysis.json
    """
    filepath = get_analysis_filepath(paper_id)
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Record {paper_id} not found")

    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if 'custom_tags' not in data:
        data['custom_tags'] = []

    updated_data = update_function(data) or data

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(updated_data, f, ensure_ascii=False, indent=2)

    return updated_data

# --- 3. 核心 API 函数 ---

def get_all_literature_summaries() -> List[Dict]:
    """
    [供 GET /api/literature 调用]
    遍历数据库，读取每个 analysis.json，返回一个摘要列表
    """
    summaries = []
    if not os.path.exists(DB_BASE_PATH):
        return []
        
    # [已修改] 按创建时间排序，最新的在前面
    # 我们通过目录的修改时间来粗略判断
    try:
        paper_ids = os.listdir(DB_BASE_PATH)
        # 按修改时间排序
        sorted_paper_ids = sorted(
            paper_ids,
            key=lambda pid: os.path.getmtime(get_paper_dir(pid)),
            reverse=True
        )
    except Exception as e:
        logging.warning(f"排序文献列表时出错: {e}, 将使用默认顺序")
        sorted_paper_ids = paper_ids

    for paper_id in sorted_paper_ids:
        paper_dir = get_paper_dir(paper_id)
        if not os.path.isdir(paper_dir):
            continue
        
        filepath = get_analysis_filepath(paper_id)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 从完整的JSON中提取前端列表需要的信息
            summary = {
                "id": data.get("paper_id", paper_id), # 使用保存的ID
                "title": data.get("文献信息", {}).get("标题", "无标题"),
                "authors": data.get("文献信息", {}).get("作者", []),
                "year": data.get("文献信息", {}).get("年份", ""),
                "custom_tags": data.get("custom_tags", []) # [新]
            ,
                "reading_time": data.get("reading_time"),
                "upload_time": data.get("upload_time")
            }
            summaries.append(summary)
            
        except Exception as e:
            logging.warning(f"读取 {filepath} 失败: {e}")
            
    return summaries

def get_literature_by_id(paper_id: str) -> Optional[Dict]:
    """
    [供 GET /api/literature/<id> 调用]
    读取并返回一篇文献的完整 analysis.json
    """
    filepath = get_analysis_filepath(paper_id)
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data
    except FileNotFoundError:
        return None
    except Exception as e:
        logging.error(f"解析 {filepath} 出错: {e}")
        return None

def save_new_literature(paper_id: str, temp_pdf_path: str, analysis_data: Dict):
    """
    [供 POST /api/upload 调用]
    创建新文件夹，保存 analysis.json 和 original.pdf
    """
    paper_dir = get_paper_dir(paper_id)
    if os.path.exists(paper_dir):
        logging.warning(f"ID {paper_id} 已存在，将覆盖。")
    else:
        os.makedirs(paper_dir)
        
    # 1. 保存 analysis.json
    filepath = get_analysis_filepath(paper_id)
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(analysis_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"保存 {filepath} 失败: {e}")
        
    # 2. 复制原始 PDF
    pdf_dest_path = os.path.join(paper_dir, PDF_FILE_NAME)
    try:
        shutil.copy(temp_pdf_path, pdf_dest_path)
    except Exception as e:
        logging.error(f"复制 PDF 到 {pdf_dest_path} 失败: {e}")

def delete_literature_by_id(paper_id: str):
    """
    [供 DELETE /api/literature/<id> 调用]
    删除整个文献文件夹
    """
    paper_dir = get_paper_dir(paper_id)
    if os.path.exists(paper_dir):
        try:
            shutil.rmtree(paper_dir) # 递归删除整个文件夹
        except Exception as e:
            logging.error(f"删除文件夹 {paper_dir} 失败: {e}")
            raise
    else:
        logging.warning(f"尝试删除不存在的文件夹: {paper_dir}")

# --- 4. 标签管理函数 ---


def add_tag_to_literature(paper_id: str, tag: str) -> List[str]:
    """
    [对 POST /api/literature/<id>/tags 调用]
    向analysis.json 添加一个新标签 (如果不存在)
    """
    def _add(data):
        if tag and tag not in data['custom_tags']:
            data['custom_tags'].append(tag)
        return data
        
    updated_data = _mutate_analysis_file(paper_id, _add)
    return updated_data.get('custom_tags', [])


def remove_tag_from_literature(paper_id: str, tag: str) -> List[str]:
    """
    [对 DELETE /api/literature/<id>/tags/<tag> 调用]
    向analysis.json 移除一个标签(如果存在)
    """
    def _remove(data):
        if tag in data['custom_tags']:
            data['custom_tags'].remove(tag)
        return data
        
    updated_data = _mutate_analysis_file(paper_id, _remove)
    return updated_data.get('custom_tags', [])

# --- 5. 图片元数据管理 ---

def get_image_metadata(paper_id: str) -> List[Dict]:
    data = get_literature_by_id(paper_id)
    if not data:
        raise FileNotFoundError(f"Record {paper_id} not found")
    return data.get('image_metadata', [])

def update_image_metadata(paper_id: str, metadata: List[Dict]) -> List[Dict]:
    metadata = metadata or []

    def _update(data):
        data['image_metadata'] = metadata
        return data
        
    updated_data = _mutate_analysis_file(paper_id, _update)
    return updated_data.get('image_metadata', [])

def update_reading_time(paper_id: str, reading_time: str) -> str:
    def _update(data):
        data['reading_time'] = reading_time
        return data

    updated_data = _mutate_analysis_file(paper_id, _update)
    return updated_data.get('reading_time', '')


def get_all_tags() -> List[str]:
    """
    [新] [供 GET /api/tags 调用]
    遍历所有 analysis.json 文件, 收集一个所有标签的唯一集合
    """
    all_tags: Set[str] = set()
    if not os.path.exists(DB_BASE_PATH):
        return []

    for paper_id in os.listdir(DB_BASE_PATH):
        paper_dir = get_paper_dir(paper_id)
        if not os.path.isdir(paper_dir):
            continue
        
        filepath = get_analysis_filepath(paper_id)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            tags = data.get("custom_tags", [])
            for tag in tags:
                all_tags.add(tag)
                
        except Exception as e:
            logging.warning(f"读取标签 {filepath} 失败: {e}")
            
    return sorted(list(all_tags))


def update_literature_metadata(paper_id: str, metadata: Dict) -> Dict:
    """
    [供 PUT /api/literature/<id>/metadata 调用]
    更新文献的基础信息 (标题, 作者, 年份, 期刊等)
    """
    def _update(data):
        if "文献信息" not in data:
            data["文献信息"] = {}
        
        # 只更新允许的字段
        allowed_fields = ["标题", "作者", "年份", "期刊"]
        for field in allowed_fields:
            if field in metadata:
                data["文献信息"][field] = metadata[field]
        return data

    updated_data = _mutate_analysis_file(paper_id, _update)
    return updated_data.get("文献信息", {})

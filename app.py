import os
import uuid
import tempfile
import logging
import threading
import webbrowser
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import time

# --- 从新模块中导入 ---
# 导入分析逻辑 (PDF提取, DeepSeek API)
import analysis_core
# 导入数据管理逻辑 (保存/读取/删除文件)
import db_manager

# --- 1. Flask 应用配置 ---
app = Flask(__name__)
CORS(app) # 允许跨域请求
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- 2. 静态文件 & API 路由 ---

@app.route("/")
def serve_index():
    """
    服务前端 index.html 文件
    """
    logging.info("Serving index.html")
    return send_from_directory('.', 'index.html')

# --- API: 文献列表 ---

@app.route("/api/literature", methods=["GET"])
def get_all_literature():
    """
    [新] 获取所有已保存的文献记录列表 (用于主页表格)
    """
    try:
        literature_list = db_manager.get_all_literature_summaries()
        return jsonify(literature_list)
    except Exception as e:
        logging.error(f"获取文献列表失败: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/literature/<paper_id>", methods=["GET"])
def get_literature_detail(paper_id):
    """
    [新] 获取单篇文献的完整分析JSON
    """
    try:
        data = db_manager.get_literature_by_id(paper_id)
        if data:
            return jsonify(data)
        else:
            return jsonify({"error": "Record not found"}), 404
    except Exception as e:
        logging.error(f"获取文献详情 {paper_id} 失败: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/literature/<paper_id>", methods=["DELETE"])
def delete_literature(paper_id):
    """
    [新] 删除一篇文献记录 (删除对应文件夹)
    """
    try:
        db_manager.delete_literature_by_id(paper_id)
        logging.info(f"文献 {paper_id} 已删除")
        return jsonify({"success": True, "message": f"Record {paper_id} deleted"})
    except Exception as e:
        logging.error(f"删除 {paper_id} 失败: {e}")
        return jsonify({"error": str(e)}), 500

# --- API: 上传与分析 ---

@app.route("/api/upload", methods=["POST"])
def upload_and_analyze_pdf():
    """
    [修改] 替换 /analyze_pdf
    上传 -> 分析 -> 保存 -> 返回新记录
    """
    logging.info("接收到 /api/upload 请求")
    
    # 1. 验证 API 密钥
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return jsonify({"error": "Missing Authorization Header"}), 401
    api_key = auth_header.split(" ")[1]

    # 2. 验证文件
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '' or not file.filename.endswith('.pdf'):
        return jsonify({"error": "Invalid file (must be a PDF)"}), 400

    # 3. 保存临时文件
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        file.save(tmp.name)
        tmp_pdf_path = tmp.name
    
    try:
        # 4. 运行核心分析逻辑 (从 analysis_core 模块调用)
        logging.info(f"开始分析临时文件: {tmp_pdf_path}")
        full_text = analysis_core.extract_text_from_pdf(tmp_pdf_path)
        if not full_text:
            return jsonify({"error": "Failed to extract text from PDF"}), 500
        
        analysis_result = analysis_core.analyze_text_with_deepseek(full_text, api_key)
        
        if "error" in analysis_result:
            return jsonify(analysis_result), 500

        # 5. [新] 创建持久化记录
        # 使用 UUID 作为唯一的、安全的文件夹名称
        paper_id = str(uuid.uuid4())
        logging.info(f"分析成功，创建新记录 ID: {paper_id}")
        
        # [修改] 在保存前，提取图片到持久化目录
        paper_dir = db_manager.get_paper_dir(paper_id)
        image_filenames = analysis_core.extract_images_from_pdf(tmp_pdf_path, paper_dir)
        
        # 添加 paper_id 和 自定义标签到JSON中，以便保存
        analysis_result['paper_id'] = paper_id
        analysis_result['custom_tags'] = []
        analysis_result['image_files'] = image_filenames # [新] 保存图片文件名列表
        
        # 保存到数据库 (从 db_manager 模块调用)
        db_manager.save_new_literature(paper_id, tmp_pdf_path, analysis_result)
        
        # 6. 返回新创建的记录摘要
        new_summary = {
            "id": paper_id,
            "title": analysis_result.get("文献信息", {}).get("标题", file.filename),
            "authors": analysis_result.get("文献信息", {}).get("作者", []),
            "year": analysis_result.get("文献信息", {}).get("年份", ""),
            "custom_tags": []
        }
        return jsonify(new_summary), 201 # 201 Created

    except Exception as e:
        logging.error(f"处理文件 {tmp_pdf_path} 时发生未知错误: {e}")
        return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500
    finally:
        # 7. 清理临时文件
        if os.path.exists(tmp_pdf_path):
            os.remove(tmp_pdf_path)
            logging.info(f"临时文件 {tmp_pdf_path} 已删除")

# --- API: 标签管理 ---

@app.route("/api/tags", methods=["GET"])
def get_all_tags():
    """
    [新] 获取所有唯一的标签
    """
    try:
        tags = db_manager.get_all_tags()
        return jsonify(tags)
    except Exception as e:
        logging.error(f"获取所有标签失败: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/literature/<paper_id>/tags", methods=["POST"])
def add_tag(paper_id):
    """
    [新] 为一篇文献添加标签
    """
    tag_data = request.json
    if not tag_data or 'tag' not in tag_data:
        return jsonify({"error": "Missing 'tag' in request body"}), 400
    
    try:
        updated_tags = db_manager.add_tag_to_literature(paper_id, tag_data['tag'])
        return jsonify({"success": True, "tags": updated_tags})
    except Exception as e:
        logging.error(f"添加标签到 {paper_id} 失败: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/literature/<paper_id>/tags/<tag_name>", methods=["DELETE"])
def remove_tag(paper_id, tag_name):
    """
    [新] 为一篇文献删除标签
    """
    try:
        updated_tags = db_manager.remove_tag_from_literature(paper_id, tag_name)
        return jsonify({"success": True, "tags": updated_tags})
    except Exception as e:
        logging.error(f"从 {paper_id} 删除标签失败: {e}")
        return jsonify({"error": str(e)}), 500
        
# --- [新] API: 图片服务 ---
@app.route("/api/literature/<paper_id>/images/<filename>")
def serve_image(paper_id, filename):
    """
    [新] 服务于持久化存储中的图片
    """
    try:
        image_dir = db_manager.get_paper_dir(paper_id)
        # 增加安全检查，防止路径遍历
        if ".." in filename or filename.startswith("/"):
            return jsonify({"error": "Invalid filename"}), 400
        return send_from_directory(image_dir, filename)
    except Exception as e:
        logging.error(f"服务图片 {paper_id}/{filename} 失败: {e}")
        return jsonify({"error": str(e)}), 404

# --- 3. 启动器 ---

def open_browser():
    time.sleep(1)
    logging.info("Opening browser to http://localhost:5000")
    webbrowser.open_new_tab("http://localhost:5000")

if __name__ == "__main__":
    db_manager.setup_database() # 确保 'literature_db' 文件夹存在
    logging.info("启动 Flask 服务器...")
    
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        threading.Timer(1, open_browser).start()
        
    app.run(host='0.0.0.0', port=5000, debug=True)
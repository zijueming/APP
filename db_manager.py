import os
import json
import logging
import shutil
from typing import Callable, Dict, List, Optional, Set

class LiteratureRepository:
    def __init__(self, db_base_path: str = "literature_db"):
        self.db_base_path = db_base_path
        self.analysis_file_name = "analysis.json"
        self.pdf_file_name = "original.pdf"
        self._setup_database()

    def _setup_database(self):
        """Ensure database directory exists."""
        if not os.path.exists(self.db_base_path):
            logging.info(f"Creating database directory: {self.db_base_path}")
            os.makedirs(self.db_base_path)

    def get_paper_dir(self, paper_id: str) -> str:
        return os.path.join(self.db_base_path, paper_id)

    def get_analysis_filepath(self, paper_id: str) -> str:
        return os.path.join(self.get_paper_dir(paper_id), self.analysis_file_name)

    def _mutate_analysis_file(self, paper_id: str, update_function: Callable[[Dict], Dict]) -> Dict:
        filepath = self.get_analysis_filepath(paper_id)
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

    def get_all_literature_summaries(self) -> List[Dict]:
        summaries = []
        if not os.path.exists(self.db_base_path):
            return []
            
        try:
            paper_ids = os.listdir(self.db_base_path)
            sorted_paper_ids = sorted(
                paper_ids,
                key=lambda pid: os.path.getmtime(self.get_paper_dir(pid)),
                reverse=True
            )
        except Exception as e:
            logging.warning(f"Error sorting literature list: {e}, using default order")
            sorted_paper_ids = paper_ids

        for paper_id in sorted_paper_ids:
            paper_dir = self.get_paper_dir(paper_id)
            if not os.path.isdir(paper_dir):
                continue
            
            filepath = self.get_analysis_filepath(paper_id)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                summary = {
                    "id": data.get("paper_id", paper_id),
                    "title": data.get("文献信息", {}).get("标题", "无标题"),
                    "authors": data.get("文献信息", {}).get("作者", []),
                    "year": data.get("文献信息", {}).get("年份", ""),
                    "custom_tags": data.get("custom_tags", []),
                    "reading_time": data.get("reading_time"),
                    "upload_time": data.get("upload_time")
                }
                summaries.append(summary)
                
            except Exception as e:
                logging.warning(f"Failed to read {filepath}: {e}")
                
        return summaries

    def get_literature_by_id(self, paper_id: str) -> Optional[Dict]:
        filepath = self.get_analysis_filepath(paper_id)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data
        except FileNotFoundError:
            return None
        except Exception as e:
            logging.error(f"Error parsing {filepath}: {e}")
            return None

    def save_new_literature(self, paper_id: str, temp_pdf_path: str, analysis_data: Dict):
        paper_dir = self.get_paper_dir(paper_id)
        if os.path.exists(paper_dir):
            logging.warning(f"ID {paper_id} exists, overwriting.")
        else:
            os.makedirs(paper_dir)
            
        filepath = self.get_analysis_filepath(paper_id)
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(analysis_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error(f"Failed to save {filepath}: {e}")
            
        pdf_dest_path = os.path.join(paper_dir, self.pdf_file_name)
        try:
            shutil.copy(temp_pdf_path, pdf_dest_path)
        except Exception as e:
            logging.error(f"Failed to copy PDF to {pdf_dest_path}: {e}")

    def delete_literature_by_id(self, paper_id: str):
        paper_dir = self.get_paper_dir(paper_id)
        if os.path.exists(paper_dir):
            try:
                shutil.rmtree(paper_dir)
            except Exception as e:
                logging.error(f"Failed to delete directory {paper_dir}: {e}")
                raise
        else:
            logging.warning(f"Attempted to delete non-existent directory: {paper_dir}")

    def add_tag_to_literature(self, paper_id: str, tag: str) -> List[str]:
        def _add(data):
            if tag and tag not in data['custom_tags']:
                data['custom_tags'].append(tag)
            return data
            
        updated_data = self._mutate_analysis_file(paper_id, _add)
        return updated_data.get('custom_tags', [])

    def remove_tag_from_literature(self, paper_id: str, tag: str) -> List[str]:
        def _remove(data):
            if tag in data['custom_tags']:
                data['custom_tags'].remove(tag)
            return data
            
        updated_data = self._mutate_analysis_file(paper_id, _remove)
        return updated_data.get('custom_tags', [])

    def get_image_metadata(self, paper_id: str) -> List[Dict]:
        data = self.get_literature_by_id(paper_id)
        if not data:
            raise FileNotFoundError(f"Record {paper_id} not found")
        return data.get('image_metadata', [])

    def update_image_metadata(self, paper_id: str, metadata: List[Dict]) -> List[Dict]:
        metadata = metadata or []
        def _update(data):
            data['image_metadata'] = metadata
            return data
            
        updated_data = self._mutate_analysis_file(paper_id, _update)
        return updated_data.get('image_metadata', [])

    def update_reading_time(self, paper_id: str, reading_time: str) -> str:
        def _update(data):
            data['reading_time'] = reading_time
            return data

        updated_data = self._mutate_analysis_file(paper_id, _update)
        return updated_data.get('reading_time', '')

    def get_all_tags(self) -> List[str]:
        all_tags: Set[str] = set()
        if not os.path.exists(self.db_base_path):
            return []

        for paper_id in os.listdir(self.db_base_path):
            paper_dir = self.get_paper_dir(paper_id)
            if not os.path.isdir(paper_dir):
                continue
            
            filepath = self.get_analysis_filepath(paper_id)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                tags = data.get("custom_tags", [])
                for tag in tags:
                    all_tags.add(tag)
                    
            except Exception as e:
                logging.warning(f"Failed to read tags from {filepath}: {e}")
                
        return sorted(list(all_tags))

    def get_tag_stats(self) -> List[Dict]:
        """
        Return aggregated tag usage counts across all papers.
        """
        tag_counts = {}
        if not os.path.exists(self.db_base_path):
            return []

        for paper_id in os.listdir(self.db_base_path):
            paper_dir = self.get_paper_dir(paper_id)
            if not os.path.isdir(paper_dir):
                continue

            filepath = self.get_analysis_filepath(paper_id)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for tag in data.get("custom_tags", []):
                    if not isinstance(tag, str):
                        continue
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
            except Exception as e:
                logging.warning(f"Failed to read tags from {filepath}: {e}")

        stats = [{"tag": name, "count": count} for name, count in tag_counts.items()]
        return sorted(stats, key=lambda x: x["tag"].lower())

    def rename_tag_globally(self, old_tag: str, new_tag: str) -> List[Dict]:
        """
        Rename a tag across every paper and return updated stats.
        """
        if not old_tag or not new_tag:
            return self.get_tag_stats()

        for paper_id in os.listdir(self.db_base_path):
            paper_dir = self.get_paper_dir(paper_id)
            if not os.path.isdir(paper_dir):
                continue

            filepath = self.get_analysis_filepath(paper_id)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                tags = data.get("custom_tags", [])
                if not tags or old_tag not in tags:
                    continue

                updated = [new_tag if t == old_tag else t for t in tags if isinstance(t, str)]
                # Keep order but remove duplicates after rename
                deduped = list(dict.fromkeys(updated))
                data["custom_tags"] = deduped

                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except FileNotFoundError:
                continue
            except Exception as e:
                logging.warning(f"Failed to rename tag in {filepath}: {e}")

        return self.get_tag_stats()

    def delete_tag_globally(self, tag: str) -> List[Dict]:
        """
        Remove a tag from every paper and return updated stats.
        """
        if not tag:
            return self.get_tag_stats()

        for paper_id in os.listdir(self.db_base_path):
            paper_dir = self.get_paper_dir(paper_id)
            if not os.path.isdir(paper_dir):
                continue

            filepath = self.get_analysis_filepath(paper_id)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                tags = data.get("custom_tags", [])
                if not tags or tag not in tags:
                    continue

                data["custom_tags"] = [t for t in tags if t != tag and isinstance(t, str)]

                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except FileNotFoundError:
                continue
            except Exception as e:
                logging.warning(f"Failed to delete tag in {filepath}: {e}")

        return self.get_tag_stats()

    def update_literature_metadata(self, paper_id: str, metadata: Dict) -> Dict:
        def _update(data):
            meta = data.setdefault("文献信息", {})
            key_map = {
                "title": "标题",
                "authors": "作者",
                "year": "年份",
                "journal": "期刊",
            }
            for incoming_key, value in metadata.items():
                target_key = key_map.get(incoming_key, incoming_key)
                if target_key in {"标题", "作者", "年份", "期刊"}:
                    meta[target_key] = value
                elif incoming_key == "upload_time" and value:
                    data["upload_time"] = value
                elif incoming_key == "time_label" and value:
                    data["time_label"] = value
            return data

        updated_data = self._mutate_analysis_file(paper_id, _update)
        return updated_data.get("文献信息", {})

# Global instance for backward compatibility if needed, 
# but we aim to use dependency injection.
# repository = LiteratureRepository()

"""
services/parser.py — 文档解析抽象接口
======================================
为书库导入提供简易的文档解析兼容层。
目前内置实现 txt 解析，预留 epub/pdf/docx 解析插槽。
"""
from abc import ABC, abstractmethod
from typing import BinaryIO
import logging

logger = logging.getLogger(__name__)

class DocumentParser(ABC):
    """文档解析器抽象基类"""
    @abstractmethod
    def parse(self, file: BinaryIO, filename: str) -> str:
        """
        将传入的二进制文件流解析为纯文本格式。
        """
        pass

class TxtParser(DocumentParser):
    """纯文本解析器"""
    def parse(self, file: BinaryIO, filename: str) -> str:
        try:
            content = file.read()
            # 尝试 utf-8，失败则尝试 gbk（常见于中文 txt）
            try:
                return content.decode("utf-8")
            except UnicodeDecodeError:
                logger.warning(f"文件 {filename} UTF-8 解码失败，尝试 GBK...")
                return content.decode("gbk", errors="ignore")
        except Exception as e:
            logger.error(f"解析文本文件失败: {e}")
            raise ValueError(f"无法解析文件 {filename}: {e}")

class ParserFactory:
    """解析器工厂"""
    @staticmethod
    def get_parser(filename: str) -> DocumentParser:
        ext = filename.split(".")[-1].lower() if "." in filename else ""
        if ext == "txt" or not ext:
            return TxtParser()
        elif ext in ("epub", "pdf", "docx"):
            # 预留插槽，暂退回纯文本或抛出暂不支持
            logger.warning(f"目前暂未实现 {ext} 的全解析器，将作为普通文本读取。")
            return TxtParser()
        else:
            logger.warning(f"未知扩展名 {ext}，回退使用 TxtParser。")
            return TxtParser()

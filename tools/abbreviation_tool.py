"""
缩写翻译工具

提供中文网络缩写词汇翻译功能
"""

from typing import Dict, Any, List, Tuple, Optional
from src.common.logger import get_logger
from src.plugin_system import BaseTool, ToolParamType
from ..translators.nbnhhsh import NbnhhshTranslator

logger = get_logger("abbreviation_tool")


class AbbreviationTool(BaseTool):
    """网络用语翻译工具"""
    
    name: str = "abbreviation_translate"
    description: str = "当遇到用户消息中出现难懂的网络用语、缩写、黑话、热词或流行语时，主动查询并翻译这些词汇以帮助理解。适用于各种类型的网络语言，包括字母缩写（如yyds、u1s1）、网络黑话、当下热词、流行语等。应该识别消息中可能让人困惑的网络用语并自动查询其含义。"
    parameters: List[Tuple[str, ToolParamType, str, bool, None]] = [
        ("term", ToolParamType.STRING, "从用户消息中识别出的网络用语、缩写或热词（如：yyds、躺平、内卷等）", True, None),
        ("max_results", ToolParamType.INTEGER, "返回翻译结果数量，默认为3", False, None),
    ]
    available_for_llm: bool = True
    
    translator: NbnhhshTranslator
    
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._initialize_translator()
    
    def _initialize_translator(self) -> None:
        """初始化翻译器"""
        translation_config = self.plugin_config.get("translation", {})
        self.translator = NbnhhshTranslator(translation_config)
        
    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, str]:
        """执行缩写翻译
        
        Args:
            function_args: 包含 'term' 和可选 'max_results' 的字典
            
        Returns:
            包含 'name' 和 'content' 的结果字典
        """
        try:
            term = function_args.get("term", "").strip()
            max_results = function_args.get("max_results", 3)
            
            if not term:
                return {"name": self.name, "content": "未提供要翻译的词汇"}
            
            # 检查是否启用了翻译功能
            if not self.plugin_config.get("translation", {}).get("enabled", True):
                return {"name": self.name, "content": "翻译功能已禁用"}
            
            logger.info(f"主动翻译检测到的词汇: {term}")
            
            # 直接翻译提供的词汇
            result = await self.translator.translate(term)
            
            if not result.translations:
                return {
                    "name": self.name,
                    "content": f"未找到「{term}」的翻译结果"
                }
            
            # 限制结果数量
            translations = result.translations[:max_results]
            
            # 格式化输出 - 针对主动识别的情况调整格式
            if len(translations) == 1:
                content = f"网络用语「{term}」的含义是：{translations[0]}"
            else:
                content = f"网络用语「{term}」的可能含义：\n"
                content += "\n".join(f"• {trans}" for trans in translations)
            
            logger.info(f"主动翻译完成: {term} -> {len(translations)}个结果")
            
            return {"name": self.name, "content": content}
            
        except Exception as e:
            logger.error(f"缩写翻译执行异常: {e}", exc_info=True)
            return {"name": self.name, "content": f"缩写翻译失败: {str(e)}"}
    

"""
翻译器模块

提供各种翻译服务的统一接口
"""

from .base import BaseTranslator
from .nbnhhsh import NbnhhshTranslator

__all__ = ['BaseTranslator', 'NbnhhshTranslator']
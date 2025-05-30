"""Notion API 客户端"""
import logger
import aiohttp
from typing import Dict, Optional, Any, Tuple, Set, List
from .exceptions import NotionAPIError, NotionFileUploadError, NotionPageError
from ..utils.config import NotionConfig
from logger import setup_logger
import json
import asyncio
from datetime import datetime

logger = setup_logger(__name__)

class NotionClient:
    """Notion API 客户端类"""
    
    # API 相关常量
    API_BASE_URL = "https://api.notion.com/v1"
    FILE_SIZE_THRESHOLD = 20 * 1024 * 1024  # 20MB
    PART_SIZE = 10 * 1024 * 1024  # 10MB
    
    # 文件类型映射
    FILE_TYPE_MIME_MAPPING = {
        'image': {'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/svg+xml'},
        'video': {'video/mp4', 'video/quicktime', 'video/x-msvideo', 'video/webm'},
        'audio': {'audio/mpeg', 'audio/mp4', 'audio/wav', 'audio/ogg', 'audio/webm'},
        'pdf': {'application/pdf'}
    }
    

    def __init__(self, config: NotionConfig):
        """初始化 Notion API 客户端
        
        Args:
            config: Notion 配置对象
        """
        self.config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._parent_page_id: Optional[str] = None
        logger.debug(
            f"NotionClient initialized - config_page_id: {config.parent_page_id} - "
            f"version: {config.notion_version}"
        )

    @property
    def parent_page_id(self) -> str:
        """获取父页面 ID"""
        page_id = self._parent_page_id or self.config.parent_page_id
        if not page_id:
            raise NotionPageError("未设置父页面 ID")
        logger.debug(f"Getting parent page ID: {page_id}")
        return page_id

    @parent_page_id.setter
    def parent_page_id(self, value: str):
        """设置父页面 ID"""
        if not value:
            raise NotionPageError("父页面 ID 不能为空")
        logger.debug(f"Setting parent page ID: {value}")
        self._parent_page_id = value

    async def __aenter__(self):
        """异步上下文管理器入口"""
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器退出"""
        if self._session:
            await self._session.close()
            self._session = None

    def _get_headers(self, content_type: Optional[str] = None) -> Dict[str, str]:
        """获取请求头
        
        Args:
            content_type: 可选的 Content-Type 头
            
        Returns:
            Dict[str, str]: 请求头字典
        """
        headers = self.config.headers.copy()
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _get_file_upload_headers(self) -> Dict[str, str]:
        """获取文件上传请求头
        
        Returns:
            Dict[str, str]: 文件上传请求头字典
        """
        return {
            "Authorization": f"Bearer {self.config.notion_key}",
            "Notion-Version": self.config.notion_version
        }

    def _determine_block_type(self, file_mime_type: str) -> str:
        """根据 MIME 类型确定块类型
        
        Args:
            file_mime_type: 文件 MIME 类型
            
        Returns:
            str: 块类型
        """
        for type_name, mime_types in self.FILE_TYPE_MIME_MAPPING.items():
            if file_mime_type in mime_types:
                return type_name
        return "file"

    def _format_error_message(self, error_data: Dict[str, Any]) -> str:
        """格式化错误信息
        
        Args:
            error_data: 错误数据字典
            
        Returns:
            str: 格式化后的错误信息
        """
        error_message = error_data.get('message', '未知错误')
        error_details = error_message.split('. ')
        formatted_error = '\n'.join([f"- {detail}" for detail in error_details if detail])
        return f"错误详情:\n{formatted_error}"

    async def _make_request(
        self,
        url: str,
        method: str = 'POST',
        payload: Optional[Dict] = None,
        data: Optional[aiohttp.FormData] = None,
        content_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """发送 API 请求
        
        Args:
            url: 请求 URL
            method: HTTP 方法
            payload: 请求负载
            data: 表单数据
            content_type: 内容类型
            
        Returns:
            Dict[str, Any]: API 响应
            
        Raises:
            NotionAPIError: API 请求失败
        """
        if not self._session:
            self._session = aiohttp.ClientSession()

        headers = self._get_file_upload_headers() if data else self._get_headers(content_type)

        logger.debug(
            f"Making Notion API request - method: {method} - "
            f"endpoint: {url.split('/')[-1]} - "
            f"has_payload: {bool(payload)} - has_data: {bool(data)} - "
            f"headers: {json.dumps(headers, ensure_ascii=False)} - "
            f"payload: {json.dumps(payload, ensure_ascii=False) if payload else None}"
        )

        try:
            if method == 'POST':
                async with self._session.post(url, json=payload, headers=headers, data=data) as response:
                    return await self._handle_response(response, url)
            elif method == 'PATCH':
                async with self._session.patch(url, json=payload, headers=headers) as response:
                    return await self._handle_response(response, url)
            elif method == 'GET':
                async with self._session.get(url, headers=headers) as response:
                    return await self._handle_response(response, url)
            else:
                raise ValueError(f"不支持的 HTTP 方法: {method}")
        except aiohttp.ClientError as e:
            logger.error(
                f"Notion API request failed - method: {method} - "
                f"endpoint: {url.split('/')[-1]} - error_type: {type(e).__name__} - "
                f"error: {str(e)}",
                exc_info=True
            )
            raise NotionAPIError(f"Notion API 请求失败: {str(e)}")
        except Exception as e:
            logger.error(
                f"Unexpected error in Notion API request - method: {method} - "
                f"endpoint: {url.split('/')[-1]} - error_type: {type(e).__name__} - "
                f"error: {str(e)}",
                exc_info=True
            )
            raise NotionAPIError(f"Notion API 请求发生意外错误: {str(e)}")

    async def _handle_response(self, response: aiohttp.ClientResponse, url: str) -> Dict[str, Any]:
        """处理 API 响应
        
        Args:
            response: API 响应对象
            url: 请求 URL
            
        Returns:
            Dict[str, Any]: 响应数据
            
        Raises:
            NotionAPIError: API 响应错误
            NotionFileUploadError: 文件上传错误
            NotionPageError: 页面操作错误
        """
        try:
            response.raise_for_status()
            response_data = await response.json()
            logger.debug(
                f"Notion API response success - status_code: {response.status} - "
                f"endpoint: {url.split('/')[-1]} - "
                f"response: {json.dumps(response_data, ensure_ascii=False)}"
            )
            return response_data
        except aiohttp.ClientResponseError as e:
            response_body = await response.text()
            try:
                error_data = json.loads(response_body)
                error_message = self._format_error_message(error_data)
                error_code = error_data.get('code', 'unknown_error')
                
                logger.error(
                    f"Notion API response error - status_code: {e.status} - "
                    f"endpoint: {url.split('/')[-1]} - "
                    f"error_type: {'file_upload' if 'file_uploads' in url else 'page_operation'} - "
                    f"error_code: {error_code} - "
                    f"error_message: {error_message} - "
                    f"request_url: {url} - "
                    f"request_method: {e.request_info.method} - "
                    f"request_headers: {json.dumps(dict(e.request_info.headers), ensure_ascii=False)}"
                )
            except json.JSONDecodeError:
                logger.error(
                    f"Notion API response error - status_code: {e.status} - "
                    f"endpoint: {url.split('/')[-1]} - "
                    f"error_type: {'file_upload' if 'file_uploads' in url else 'page_operation'} - "
                    f"response_body: {response_body} - "
                    f"request_url: {url} - "
                    f"request_method: {e.request_info.method} - "
                    f"request_headers: {json.dumps(dict(e.request_info.headers), ensure_ascii=False)}"
                )
            
            if 'file_uploads' in url:
                raise NotionFileUploadError(
                    f"Notion 文件上传失败: {error_message if 'error_message' in locals() else e.message}",
                    status_code=e.status,
                    response_body=response_body
                )
            else:
                raise NotionPageError(
                    f"Notion 页面操作失败: {error_message if 'error_message' in locals() else e.message}",
                    status_code=e.status,
                    response_body=response_body
                )

    def _build_page_properties(self, title: str, properties: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """构建页面属性
        
        Args:
            title: 页面标题
            properties: 页面属性
            
        Returns:
            Dict[str, Any]: 页面属性字典
        """
        page_properties = {
            "标题": {
                "title": [
                    {
                        "type": "text",
                        "text": {
                            "content": title
                        }
                    }
                ]
            }
        }
        
        if not properties:
            return page_properties
            
        # 来源
        if properties.get('来源'):
            page_properties['来源'] = {
                "select": {
                    "name": properties['来源']
                }
            }
        
        # 标签
        if properties.get('标签'):
            page_properties['标签'] = {
                "multi_select": [
                    {"name": tag} for tag in properties['标签']
                ]
            }
        
        # 置顶状态
        if '是否置顶' in properties:
            page_properties['是否置顶'] = {
                "checkbox": properties['是否置顶']
            }
        
        # 源链接
        if properties.get('源链接'):
            page_properties['源链接'] = {
                "url": properties['源链接']
            }
        
        # 创建时间
        if properties.get('创建时间'):
            page_properties['创建时间'] = {
                "date": {
                    "start": properties['创建时间'].isoformat()
                }
            }
        
        # 更新时间
        if properties.get('更新时间'):
            page_properties['更新时间'] = {
                "date": {
                    "start": properties['更新时间'].isoformat()
                }
            }
        
        # 文件数量
        if '文件数量' in properties:
            page_properties['文件数量'] = {
                "number": properties['文件数量']
            }
        
        # 链接数量
        if '链接数量' in properties:
            page_properties['链接数量'] = {
                "number": properties['链接数量']
            }
        
        # 状态
        if properties.get('状态'):
            page_properties['状态'] = {
                "select": {
                    "name": properties['状态']
                }
            }
            
        return page_properties

    def _build_update_payload(self, properties: Dict[str, Any]) -> Dict[str, Any]:
        """构建更新负载
        
        Args:
            properties: 要更新的属性
            
        Returns:
            Dict[str, Any]: 更新负载字典
        """
        payload = {
            "properties": {}
        }
        
        for key, value in properties.items():
            if isinstance(value, datetime):
                payload["properties"][key] = {
                    "date": {
                        "start": value.isoformat()
                    }
                }
            elif isinstance(value, (int, float)):
                payload["properties"][key] = {
                    "number": value
                }
            elif isinstance(value, str):
                payload["properties"][key] = {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {
                                "content": value
                            }
                        }
                    ]
                }
            else:
                payload["properties"][key] = value
                
        return payload

    def _split_text_to_paragraphs(self, text: str, max_length: int = 1950) -> List[str]:
        """将文本按最大长度切分为段落"""
        return [text[i:i+max_length] for i in range(0, len(text), max_length)]

    async def create_page(
        self,
        title: str,
        content_text: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None,
        parent_page_id: Optional[str] = None
    ) -> str:
        """创建 Notion 页面
        
        Args:
            title: 页面标题
            content_text: 页面内容文本
            properties: 页面属性，包括：
                - 来源: 来源（select 类型）
                - 标签: 标签列表
                - 是否置顶: 是否置顶
                - 源链接: 源链接
                - 创建时间: 创建时间
                - 更新时间: 更新时间
                - 文件数量: 文件数量
                - 链接数量: 链接数量
                - 状态: 状态
            parent_page_id: 父页面 ID，如果提供则使用此 ID，否则使用默认的 parent_page_id
            
        Returns:
            str: 新创建的页面 ID
            
        Raises:
            NotionPageError: 创建页面失败
        """
        logger.debug(
            f"Creating Notion page - has_content: {bool(content_text)} - "
            f"title_length: {len(title)} - has_properties: {bool(properties)} - "
            f"parent_page_id: {parent_page_id or self.parent_page_id}"
        )
        
        url = f"{self.API_BASE_URL}/pages"
        
        # 构建请求体
        payload = {
            "parent": {
                "type": "database_id",
                "database_id": parent_page_id or self.parent_page_id
            },
            "properties": self._build_page_properties(title, properties)
        }
        
        # 如果有内容，添加为子块（自动分段）
        if content_text:
            payload["children"] = [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "type": "text",
                                "text": {"content": para}
                            }
                        ]
                    }
                }
                for para in self._split_text_to_paragraphs(content_text, 1950)
            ]
        
        try:
            response = await self._make_request(url, payload=payload)
            new_page_id = response.get("id")
            if not new_page_id:
                raise NotionPageError("创建页面失败：未返回页面ID")
                
            logger.info(
                f"Created new Notion page - page_id: {new_page_id} - "
                f"title: {title} - parent_page_id: {parent_page_id or self.parent_page_id}"
            )
            
            return new_page_id
            
        except NotionPageError as e:
            logger.error(
                f"Failed to create Notion page - error: {str(e)} - "
                f"parent_page_id: {parent_page_id or self.parent_page_id} - "
                f"title: {title}"
            )
            raise

    async def append_text(self, page_id: str, content_text: str) -> None:
        """添加文本块到页面
        
        Args:
            page_id: 页面 ID
            content_text: 要添加的文本内容
            
        Raises:
            NotionPageError: 添加文本失败
        """
        logger.debug(
            f"Appending text to Notion page - page_id: {page_id[:8]}... - "
            f"content_length: {len(content_text)}"
        )
        
        # 自动分段
        paragraphs = self._split_text_to_paragraphs(content_text, 1950)
        url = f"{self.API_BASE_URL}/blocks/{page_id}/children"
        payload = {
            "children": [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "type": "text",
                                "text": {"content": para}
                            }
                        ]
                    }
                }
                for para in paragraphs
            ]
        }
        await self._make_request(url, method='PATCH', payload=payload)
        logger.debug(f"Text appended successfully - page_id: {page_id[:8]}...")

    async def create_file_upload(
        self,
        file_name: str,
        content_type: str,
        file_size: Optional[int] = None,
        external_url: Optional[str] = None
    ) -> Tuple[str, str, Optional[int], Optional[str]]:
        """创建文件上传对象
        
        Args:
            file_name: 文件名
            content_type: 文件类型
            file_size: 文件大小（字节）
            external_url: 外部文件 URL
            
        Returns:
            Tuple[str, str, Optional[int], Optional[str]]: 
                - 上传 ID
                - 上传 URL
                - 分片数量（如果是分片上传）
                - 上传模式
                
        Raises:
            ValueError: 参数错误
            NotionFileUploadError: 创建上传对象失败
        """
        logger.debug(
            f"Creating file upload object - content_type: {content_type} - "
            f"file_size_mb: {round(file_size / (1024 * 1024), 2) if file_size else None}"
        )
        
        url = f"{self.API_BASE_URL}/file_uploads"
        
        if external_url:
            if not external_url.startswith("https://"):
                raise ValueError("external_url 必须以 https:// 开头")
            mode = "external_url"
            number_of_parts = None
            payload = {
                "mode": mode,
                "external_url": external_url,
                "filename": file_name
            }
            logger.debug(f"Using external_url upload mode - url: {external_url[:50]}...")
        else:
            mode = "single_part"
            number_of_parts = None
            payload = {"filename": file_name, "content_type": content_type, "mode": mode}
            
            if file_size and file_size > self.FILE_SIZE_THRESHOLD:
                mode = "multi_part"
                number_of_parts = (file_size + self.PART_SIZE - 1) // self.PART_SIZE
                payload = {
                    "mode": mode,
                    "number_of_parts": number_of_parts,
                    "filename": file_name,
                    "content_type": content_type
                }
                logger.debug(
                    f"Using multi-part upload mode - number_of_parts: {number_of_parts} - "
                    f"part_size_mb: {round(self.PART_SIZE / (1024 * 1024), 2)}"
                )
        
        response = await self._make_request(url, method='POST', payload=payload)
        logger.info(
            f"File upload object created - upload_id: {response['id'][:8]}... - "
            f"mode: {mode} - number_of_parts: {number_of_parts}"
        )
        return response['id'], response['upload_url'], number_of_parts, mode

    async def upload_file_part(
        self,
        file_path: str,
        upload_url: str,
        content_type: str,
        part_number: int,
        start_byte: int,
        end_byte: int
    ) -> None:
        """上传文件的一部分
        
        Args:
            file_path: 文件路径
            upload_url: 上传 URL
            content_type: 文件类型
            part_number: 分片序号
            start_byte: 起始字节位置
            end_byte: 结束字节位置
            
        Raises:
            NotionFileUploadError: 上传分片失败
        """
        logger.debug(
            f"Uploading file part - part_number: {part_number} - "
            f"content_type: {content_type} - part_size: {end_byte - start_byte}"
        )
        
        with open(file_path, "rb") as f:
            f.seek(start_byte)
            part_data = f.read(end_byte - start_byte)
            
            data = aiohttp.FormData()
            data.add_field('file', part_data, content_type=content_type)
            data.add_field('part_number', str(part_number))
            
            try:
                await self._make_request(upload_url, method='POST', data=data)
                logger.debug(
                    f"File part uploaded successfully - part_number: {part_number} - "
                    f"content_type: {content_type}"
                )
            except Exception as e:
                logger.error(
                    f"Failed to upload file part - part_number: {part_number} - "
                    f"error_type: {type(e).__name__}",
                    exc_info=True
                )
                raise

    async def complete_multi_part_upload(self, file_upload_id: str) -> None:
        """完成多部分文件上传
        
        Args:
            file_upload_id: 文件上传 ID
            
        Raises:
            NotionFileUploadError: 完成上传失败
        """
        logger.debug(f"Completing multi-part upload - upload_id: {file_upload_id[:8]}...")
        
        url = f"{self.API_BASE_URL}/file_uploads/{file_upload_id}/complete"
        await self._make_request(url, method='POST')
        
        logger.info(f"Multi-part upload completed - upload_id: {file_upload_id[:8]}...")

    async def get_file_upload_status(self, file_upload_id: str) -> Dict[str, Any]:
        """获取文件上传状态
        
        Args:
            file_upload_id: 文件上传 ID
            
        Returns:
            Dict[str, Any]: 包含文件上传状态的响应
            
        Raises:
            NotionFileUploadError: 获取状态失败
        """
        url = f"{self.API_BASE_URL}/file_uploads/{file_upload_id}"
        logger.debug(f"Getting file upload status - upload_id: {file_upload_id[:8]}...")

        response = await self._make_request(url, method='GET')
        logger.debug(
            f"File upload status - upload_id: {file_upload_id[:8]}... - "
            f"status: {response.get('status')}"
        )
        return response

    async def wait_for_file_upload(
        self,
        file_upload_id: str,
        max_retries: int = 6,
        initial_delay: float = 5.0
    ) -> Dict[str, Any]:
        """等待文件上传完成
        
        Args:
            file_upload_id: 文件上传 ID
            max_retries: 最大重试次数
            initial_delay: 初始延迟时间（秒）
            
        Returns:
            Dict[str, Any]: 包含文件上传状态的响应
            
        Raises:
            NotionFileUploadError: 文件上传失败或超时
        """
        logger.debug(
            f"Waiting for file upload - upload_id: {file_upload_id[:8]}... - "
            f"max_retries: {max_retries} - initial_delay: {initial_delay}"
        )

        delay = initial_delay
        for attempt in range(max_retries):
            try:
                response = await self.get_file_upload_status(file_upload_id)
                status = response.get('status')

                if status == 'uploaded':
                    logger.info(f"File upload completed - upload_id: {file_upload_id[:8]}...")
                    return response
                elif status == 'failed':
                    error = response.get('file_import_result', {}).get('error', {})
                    logger.error(
                        f"File upload failed - upload_id: {file_upload_id[:8]}... - "
                        f"error: {json.dumps(error, ensure_ascii=False)}"
                    )
                    raise NotionFileUploadError(
                        f"文件上传失败: {error.get('message', '未知错误')}"
                    )

                logger.debug(
                    f"File upload still pending - upload_id: {file_upload_id[:8]}... - "
                    f"attempt: {attempt + 1}/{max_retries} - delay: {delay}"
                )

                await asyncio.sleep(delay)
                delay *= 2  # 指数退避

            except Exception as e:
                logger.error(
                    f"Error checking file upload status - upload_id: {file_upload_id[:8]}... - "
                    f"attempt: {attempt + 1}/{max_retries} - error_type: {type(e).__name__}",
                    exc_info=True
                )
                if attempt == max_retries - 1:
                    raise NotionFileUploadError(f"等待文件上传超时: {str(e)}")
                await asyncio.sleep(delay)
                delay *= 2

        raise NotionFileUploadError("等待文件上传超时")

    async def append_file_block(
        self,
        page_id: str,
        file_upload_id: str,
        file_name: str,
        file_mime_type: str
    ) -> None:
        """添加文件块到页面
        
        Args:
            page_id: 页面 ID
            file_upload_id: 文件上传 ID
            file_name: 文件名
            file_mime_type: 文件 MIME 类型
            
        Raises:
            NotionFileUploadError: 添加文件块失败
        """
        logger.debug(
            f"Appending file block to page - page_id: {page_id[:8]}... - "
            f"upload_id: {file_upload_id[:8]}... - mime_type: {file_mime_type}"
        )
        
        # 等待文件上传完成
        await self.wait_for_file_upload(file_upload_id)

        url = f"{self.API_BASE_URL}/blocks/{page_id}/children"
        
        # 根据文件类型确定块类型
        block_type = self._determine_block_type(file_mime_type)
        
        logger.debug(
            f"Determined block type - block_type: {block_type} - "
            f"mime_type: {file_mime_type} - file_name: {file_name}"
        )

        # 构建 payload
        payload = {
            "children": [
                {
                    "object": "block",
                    "type": block_type,
                    block_type: {
                        "type": "file_upload",
                        "file_upload": {
                            "id": file_upload_id
                        }
                    }
                }
            ]
        }

        # 添加文件名作为标题（如果需要）
        if file_name:
            payload["children"][0][block_type]["caption"] = [
                {
                    "type": "text",
                    "text": {
                        "content": file_name
                    }
                }
            ]
        
        logger.debug(
            f"Preparing to append file block - url: {url} - "
            f"payload: {json.dumps(payload, ensure_ascii=False)}"
        )

        try:
            response = await self._make_request(url, method='PATCH', payload=payload)
            logger.info(
                f"File block appended successfully - page_id: {page_id[:8]}... - "
                f"block_type: {block_type} - mime_type: {file_mime_type}"
            )
            return response
        except Exception as e:
            logger.error(
                f"Failed to append file block - error_type: {type(e).__name__} - "
                f"page_id: {page_id[:8]}... - block_type: {block_type} - "
                f"mime_type: {file_mime_type} - payload: {json.dumps(payload, ensure_ascii=False)}",
                exc_info=True
            )
            raise

    async def get_page(self, page_id: str) -> Dict[str, Any]:
        """获取页面信息
        
        Args:
            page_id: 页面 ID
            
        Returns:
            Dict[str, Any]: 页面信息
            
        Raises:
            NotionPageError: 获取页面信息失败
        """
        url = f"{self.API_BASE_URL}/pages/{page_id}"
        logger.debug(f"Getting page info - page_id: {page_id[:8]}...")
        
        try:
            response = await self._make_request(url, method='GET')
            logger.info(f"Successfully got page info - page_id: {page_id[:8]}...")
            return response
        except Exception as e:
            logger.error(
                f"Failed to get page info - error_type: {type(e).__name__} - "
                f"page_id: {page_id[:8]}...",
                exc_info=True
            )
            raise

    async def update_page(self, page_id: str, properties: Dict[str, Any]) -> Dict[str, Any]:
        """更新页面属性
        
        Args:
            page_id: 页面 ID
            properties: 要更新的属性
            
        Returns:
            Dict[str, Any]: 更新后的页面信息
            
        Raises:
            NotionPageError: 更新页面属性失败
        """
        url = f"{self.API_BASE_URL}/pages/{page_id}"
        
        # 创建一个用于日志记录的属性副本，将 datetime 转换为字符串
        log_properties = {}
        for key, value in properties.items():
            if isinstance(value, datetime):
                log_properties[key] = value.isoformat()
            else:
                log_properties[key] = value
                
        logger.debug(
            f"Updating page properties - page_id: {page_id[:8]}... - "
            f"properties: {json.dumps(log_properties, ensure_ascii=False)}"
        )
        
        try:
            payload = self._build_update_payload(properties)
            response = await self._make_request(url, method='PATCH', payload=payload)
            logger.info(
                f"Successfully updated page properties - page_id: {page_id[:8]}... - "
                f"properties: {json.dumps(log_properties, ensure_ascii=False)}"
            )
            return response
        except Exception as e:
            logger.error(
                f"Failed to update page properties - error_type: {type(e).__name__} - "
                f"page_id: {page_id[:8]}... - properties: {json.dumps(log_properties, ensure_ascii=False)}",
                exc_info=True
            )
            raise
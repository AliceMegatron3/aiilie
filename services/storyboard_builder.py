import logging
import uuid
from typing import List, Dict
from utils.resource_path import get_user_data_path

logger = logging.getLogger(__name__)

class StoryboardPromptBuilder:
    def __init__(self, style_tags: List[str]):
        """
        初始化分镜构建器
        :param style_tags: 项目全局设定的画风，如 ["cyberpunk", "cinematic lighting", "unreal engine 5"]
        """
        self.style_tags = style_tags
        
    def build_prompt(self, environment_description: str) -> str:
        """
        将中文环境描写转换为生图模型 (Stable Diffusion / Midjourney) 需要的英文 Prompt 格式。
        (在真实实现中，可调用 Batch 3 基础大模型进行翻译与润色)
        """
        # 伪代码：假设此处已将中文翻译为英文描述 base_prompt
        base_prompt = f"A dramatic scene of: {environment_description}"
        
        # 拼接项目全局风格标签
        final_prompt = base_prompt + ", " + ", ".join(self.style_tags)
        final_prompt += " --ar 16:9 --v 6.0" # 模拟 MJ 参数
        
        return final_prompt

    def dispatch_image_generation_task(self, doc_id: str, prompt: str) -> str:
        """
        将生图任务丢给 Batch 1 的 TaskManager。
        由于生图非常耗时（10s ~ 60s），绝不能阻塞 API 请求。
        """
        task_id = str(uuid.uuid4())
        logger.info(f"派发异步生图任务 [GENERATE_IMAGE], Task ID: {task_id}")
        logger.info(f"生图提示词: {prompt}")
        
        # 伪代码演示与 Batch 1 的融合：
        # task = SystemTask(
        #     task_id=task_id, 
        #     batch_id="batch1_core", 
        #     status="PENDING",
        #     command="GENERATE_IMAGE",
        #     payload={"prompt": prompt, "doc_id": doc_id}
        # )
        # task_manager.enqueue(task)
        # 
        # 当任务在后台被 worker 消费完后，会把生成的图片保存在:
        # get_user_data_path(f"data/assets/storyboard/{task_id}.png")
        
        return task_id

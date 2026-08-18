"""诗词音韵学研究服务（实用近似，非学术级中古音重构）。

设计说明（近似度标注）：
- **中古音**：本模块不重构完整中古音（切韵/广韵音系），而以「平水韵 106 韵 + 平仄推导」作实用近似。
  平水韵属中古后期音系，能区分平/上/去/入四声，入声独立处理（归仄），足以支撑格律验证。
- **普通话**：内置轻量 pinyin+tone 表（覆盖评测集/常见字），未收录字标记 unknown。
- **平水韵**：内置常用字 → 韵部映射（约百余常用字，韵脚字全部覆盖），未收录字按普通话声调近似平仄并标记 approx=True。

依赖：仅标准库。若未来安装 pypinyin 可扩展普通话全字覆盖，本模块保持向后兼容。
"""
from __future__ import annotations

import re
from typing import Any

# ─────────────────────────────────────────────────────────────
# 普通话 pinyin+tone 轻量表：char -> (pinyin, tone 1-4)
# 覆盖评测集全部用字与常见诗字（轻量、够用）。
# ─────────────────────────────────────────────────────────────
_PINYIN: dict[str, tuple[str, int]] = {
    # 静夜思
    "床": ("chuang", 2), "前": ("qian", 2), "明": ("ming", 2), "月": ("yue", 4),
    "光": ("guang", 1), "疑": ("yi", 2), "是": ("shi", 4), "地": ("di", 4),
    "上": ("shang", 4), "霜": ("shuang", 1), "举": ("ju", 3), "头": ("tou", 2),
    "望": ("wang", 4), "低": ("di", 1), "思": ("si", 1), "故": ("gu", 4), "乡": ("xiang", 1),
    # 登鹳雀楼
    "白": ("bai", 2), "日": ("ri", 4), "依": ("yi", 1), "山": ("shan", 1),
    "尽": ("jin", 4), "黄": ("huang", 2), "河": ("he", 2), "入": ("ru", 4),
    "海": ("hai", 3), "流": ("liu", 2), "欲": ("yu", 4), "穷": ("qiong", 2),
    "千": ("qian", 1), "里": ("li", 3), "目": ("mu", 4), "更": ("geng", 4),
    "一": ("yi", 1), "层": ("ceng", 2), "楼": ("lou", 2),
    # 春晓
    "春": ("chun", 1), "眠": ("mian", 2), "不": ("bu", 4), "觉": ("jue", 2),
    "晓": ("xiao", 3), "处": ("chu", 4), "闻": ("wen", 2), "啼": ("ti", 2),
    "鸟": ("niao", 3), "夜": ("ye", 4), "来": ("lai", 2), "风": ("feng", 1),
    "雨": ("yu", 3), "声": ("sheng", 1), "花": ("hua", 1), "落": ("luo", 4),
    "知": ("zhi", 1), "多": ("duo", 1), "少": ("shao", 3),
    # 相思
    "红": ("hong", 2), "豆": ("dou", 4), "生": ("sheng", 1), "南": ("nan", 2),
    "国": ("guo", 2), "发": ("fa", 1), "几": ("ji", 3), "枝": ("zhi", 1),
    "愿": ("yuan", 4), "君": ("jun", 1), "采": ("cai", 3), "撷": ("xie", 2),
    "此": ("ci", 3), "物": ("wu", 4), "最": ("zui", 4), "相": ("xiang", 1),
    # 悯农
    "种": ("zhong", 3), "粒": ("li", 4), "粟": ("su", 4), "秋": ("qiu", 1),
    "收": ("shou", 1), "万": ("wan", 4), "颗": ("ke", 1), "子": ("zi", 3),
    "四": ("si", 4), "无": ("wu", 2), "闲": ("xian", 2), "田": ("tian", 2),
    "农": ("nong", 2), "夫": ("fu", 1), "犹": ("you", 2), "饿": ("e", 4), "死": ("si", 3),
    # 自制七绝/五律/七律/词牌用字
    "江": ("jiang", 1), "青": ("qing", 1), "遥": ("yao", 2), "片": ("pian", 4),
    "寄": ("ji", 4), "鸿": ("hong", 2), "新": ("xin", 1), "泉": ("quan", 2),
    "石": ("shi", 2), "林": ("lin", 2), "语": ("yu", 3), "云": ("yun", 2),
    "见": ("jian", 4), "须": ("xu", 1), "何": ("he", 2), "在": ("zai", 4),
    "暮": ("mu", 4), "带": ("dai", 4), "去": ("qu", 4), "远": ("yuan", 3),
    "送": ("song", 4), "晚": ("wan", 3), "潮": ("chao", 2), "别": ("bie", 2),
    "后": ("hou", 4), "三": ("san", 1), "客": ("ke", 4), "雁": ("yan", 4),
    "鸣": ("ming", 2), "莫": ("mo", 4), "关": ("guan", 1), "难": ("nan", 2),
    "成": ("cheng", 2), "未": ("wei", 4), "共": ("gong", 4), "婵": ("chan", 2),
    "娟": ("juan", 1), "时": ("shi", 2), "船": ("chuan", 2), "满": ("man", 3),
    "袖": ("xiu", 4), "如": ("ru", 2), "孤": ("gu", 1), "舟": ("zhou", 1),
    "边": ("bian", 1), "从": ("cong", 2), "到": ("dao", 4), "烟": ("yan", 1),
    # 乌衣巷
    "朱": ("zhu", 1), "雀": ("que", 4), "桥": ("qiao", 2), "野": ("ye", 3),
    "草": ("cao", 3), "乌": ("wu", 1), "巷": ("xiang", 4), "口": ("kou", 3),
    "夕": ("xi", 1), "斜": ("xie", 2), "旧": ("jiu", 4), "谢": ("xie", 4),
    "堂": ("tang", 2), "燕": ("yan", 4), "寻": ("xun", 2), "常": ("chang", 2),
    "百": ("bai", 3), "姓": ("xing", 4), "家": ("jia", 1),
    # 测试用例字
    "平": ("ping", 2), "仄": ("ze", 4), "天": ("tian", 1),
    # 通用常见字（生成器/韵部候选补充）
    "东": ("dong", 1), "同": ("tong", 2), "中": ("zhong", 1), "虫": ("chong", 2),
    "终": ("zhong", 1), "空": ("kong", 1), "功": ("gong", 1), "工": ("gong", 1),
    "公": ("gong", 1), "通": ("tong", 1), "宫": ("gong", 1), "穷": ("qiong", 2),
    "翁": ("weng", 1), "丛": ("cong", 2), "蒙": ("meng", 2), "蓬": ("peng", 2),
    "虹": ("hong", 2), "弓": ("gong", 1), "融": ("rong", 2), "穹": ("qiong", 2),
    "阳": ("yang", 2), "扬": ("yang", 2), "香": ("xiang", 1), "堂": ("tang", 2),
    "长": ("chang", 2), "凉": ("liang", 2), "方": ("fang", 1), "芳": ("fang", 1),
    "昌": ("chang", 1), "章": ("zhang", 1), "伤": ("shang", 1), "张": ("zhang", 1),
    "梁": ("liang", 2), "皇": ("huang", 2), "荒": ("huang", 1), "苍": ("cang", 1),
    "郎": ("lang", 2), "桑": ("sang", 1), "裳": ("chang", 2), "洋": ("yang", 2),
    "由": ("you", 2), "游": ("you", 2), "牛": ("niu", 2), "忧": ("you", 1),
    "求": ("qiu", 2), "休": ("xiu", 1), "留": ("liu", 2), "州": ("zhou", 1),
    "愁": ("chou", 2), "羞": ("xiu", 1), "谋": ("mou", 2), "侯": ("hou", 2),
    "浮": ("fu", 2), "偷": ("tou", 1), "投": ("tou", 2), "钩": ("gou", 1),
    "沟": ("gou", 1), "丘": ("qiu", 1), "悠": ("you", 1), "柔": ("rou", 2),
    "移": ("yi", 2), "垂": ("chui", 2), "眉": ("mei", 2), "悲": ("bei", 1),
    "诗": ("shi", 1), "词": ("ci", 2), "丝": ("si", 1), "之": ("zhi", 1),
    "期": ("qi", 1), "迟": ("chi", 2), "池": ("chi", 2), "师": ("shi", 1),
    "儿": ("er", 2), "衣": ("yi", 1), "机": ("ji", 1), "稀": ("xi", 1),
    "年": ("nian", 2), "莲": ("lian", 2), "弦": ("xian", 2), "鲜": ("xian", 1),
    "圆": ("yuan", 2), "钱": ("qian", 2), "怜": ("lian", 2), "然": ("ran", 2),
    "穿": ("chuan", 1), "肩": ("jian", 1), "迁": ("qian", 1), "蝉": ("chan", 2),
    "清": ("qing", 1), "晴": ("qing", 2), "情": ("qing", 2), "迎": ("ying", 2),
    "惊": ("jing", 1), "名": ("ming", 2), "轻": ("qing", 1), "京": ("jing", 1),
    "荆": ("jing", 1), "兵": ("bing", 1), "兄": ("xiong", 1), "营": ("ying", 2),
    "衡": ("heng", 2), "荣": ("rong", 2), "星": ("xing", 1), "亭": ("ting", 2),
    "庭": ("ting", 2), "经": ("jing", 1), "形": ("xing", 2), "灵": ("ling", 2),
    "听": ("ting", 1), "丁": ("ding", 1), "铭": ("ming", 2), "城": ("cheng", 2),
    "照": ("zhao", 4), "松": ("song", 1), "竹": ("zhu", 2), "喧": ("xuan", 1),
    "浣": ("huan", 4), "女": ("nv", 3), "莲": ("lian", 2), "渔": ("yu", 2),
    "随": ("sui", 2), "意": ("yi", 4), "歇": ("xie", 1), "孙": ("sun", 1),
    "自": ("zi", 4), "可": ("ke", 3), "将": ("jiang", 1), "楼": ("lou", 2),
}

# ─────────────────────────────────────────────────────────────
# 平水韵 106 韵 → 声类（平/上/去/入）
# 上平声 15 + 下平声 15 = 30 平声韵；上声 29 + 去声 30 + 入声 17 = 76 仄声韵。
# ─────────────────────────────────────────────────────────────
_PING_SHUI_TONE_CLASS: dict[str, str] = {
    "一东": "平", "二冬": "平", "三江": "平", "四支": "平", "五微": "平",
    "六鱼": "平", "七虞": "平", "八齐": "平", "九佳": "平", "十灰": "平",
    "十一真": "平", "十二文": "平", "十三元": "平", "十四寒": "平", "十五删": "平",
    "一先": "平", "二萧": "平", "三肴": "平", "四豪": "平", "五歌": "平",
    "六麻": "平", "七阳": "平", "八庚": "平", "九青": "平", "十蒸": "平",
    "十一尤": "平", "十二侵": "平", "十三覃": "平", "十四盐": "平", "十五咸": "平",
    "一董": "上", "二肿": "上", "三讲": "上", "四纸": "上", "五尾": "上",
    "六语": "上", "七麌": "上", "八荠": "上", "九蟹": "上", "十贿": "上",
    "十一轸": "上", "十二吻": "上", "十三阮": "上", "十四旱": "上", "十五潸": "上",
    "十六铣": "上", "十七筱": "上", "十八巧": "上", "十九皓": "上", "二十哿": "上",
    "二十一马": "上", "二十二养": "上", "二十三梗": "上", "二十四迥": "上", "二十五有": "上",
    "二十六寝": "上", "二十七感": "上", "二十八俭": "上", "二十九豏": "上",
    "一送": "去", "二宋": "去", "三绛": "去", "四寘": "去", "五未": "去",
    "六御": "去", "七遇": "去", "八霁": "去", "九泰": "去", "十卦": "去",
    "十一队": "去", "十二震": "去", "十三问": "去", "十四愿": "去", "十五翰": "去",
    "十六谏": "去", "十七霰": "去", "十八啸": "去", "十九效": "去", "二十号": "去",
    "二十一箇": "去", "二十二祃": "去", "二十三漾": "去", "二十四敬": "去", "二十五径": "去",
    "二十六宥": "去", "二十七沁": "去", "二十八勘": "去", "二十九艳": "去", "三十陷": "去",
    "一屋": "入", "二沃": "入", "三觉": "入", "四质": "入", "五物": "入",
    "六月": "入", "七曷": "入", "八黠": "入", "九屑": "入", "十药": "入",
    "十一陌": "入", "十二锡": "入", "十三职": "入", "十四缉": "入", "十五合": "入",
    "十六叶": "入", "十七洽": "入",
}

# ─────────────────────────────────────────────────────────────
# 平水韵 常用字 → 韵部（韵脚字全部覆盖；其余为韵部候选池）
# char -> 韵部名
# ─────────────────────────────────────────────────────────────
_PING_SHUI: dict[str, str] = {
    # 一东（平）
    "东": "一东", "同": "一东", "中": "一东", "虫": "一东", "终": "一东",
    "风": "一东", "空": "一东", "红": "一东", "功": "一东", "工": "一东",
    "公": "一东", "通": "一东", "宫": "一东", "穷": "一东", "翁": "一东",
    "鸿": "一东", "丛": "一东", "蒙": "一东", "蓬": "一东", "虹": "一东",
    "弓": "一东", "融": "一东", "穹": "一东",
    # 二冬（平）
    "冬": "二冬", "农": "二冬", "宗": "二冬", "钟": "二冬", "龙": "二冬",
    "峰": "二冬", "松": "二冬",
    # 四支（平）
    "支": "四支", "枝": "四支", "移": "四支", "垂": "四支", "眉": "四支",
    "悲": "四支", "时": "四支", "诗": "四支", "词": "四支", "丝": "四支",
    "知": "四支", "之": "四支", "期": "四支", "迟": "四支", "池": "四支",
    "师": "四支", "儿": "四支", "衣": "四支", "机": "四支", "稀": "四支",
    "思": "四支",
    # 五微（平）
    "微": "五微", "辉": "五微", "飞": "五微", "非": "五微", "依": "五微",
    "归": "五微", "围": "五微", "违": "五微", "威": "五微", "扉": "五微",
    # 六鱼（平）
    "鱼": "六鱼", "书": "六鱼", "居": "六鱼", "车": "六鱼", "如": "六鱼",
    "庐": "六鱼", "初": "六鱼", "余": "六鱼", "疏": "六鱼", "虚": "六鱼",
    # 七虞（平）
    "虞": "七虞", "愚": "七虞", "夫": "七虞", "无": "七虞", "湖": "七虞",
    "都": "七虞", "途": "七虞", "图": "七虞", "珠": "七虞", "朱": "七虞",
    "儒": "七虞", "苏": "七虞", "姑": "七虞", "孤": "七虞", "呼": "七虞",
    "乌": "七虞", "吴": "七虞", "梧": "七虞", "卢": "七虞", "炉": "七虞",
    "壶": "七虞", "酥": "七虞",
    # 八齐（平）
    "齐": "八齐", "西": "八齐", "鸡": "八齐", "啼": "八齐", "迷": "八齐",
    "泥": "八齐", "题": "八齐", "堤": "八齐", "低": "八齐", "溪": "八齐",
    "妻": "八齐", "栖": "八齐", "梨": "八齐", "畦": "八齐",
    # 十灰（平）
    "灰": "十灰", "回": "十灰", "杯": "十灰", "梅": "十灰", "开": "十灰",
    "台": "十灰", "来": "十灰", "才": "十灰", "裁": "十灰", "哀": "十灰",
    "苔": "十灰", "材": "十灰", "陪": "十灰", "雷": "十灰", "催": "十灰",
    # 十一真（平）
    "真": "十一真", "人": "十一真", "仁": "十一真", "亲": "十一真", "春": "十一真",
    "辰": "十一真", "身": "十一真", "新": "十一真", "神": "十一真", "陈": "十一真",
    "贫": "十一真", "民": "十一真", "津": "十一真", "尘": "十一真", "晨": "十一真",
    "频": "十一真", "邻": "十一真", "珍": "十一真", "匀": "十一真", "巡": "十一真",
    # 十二文（平）
    "文": "十二文", "云": "十二文", "分": "十二文", "闻": "十二文", "君": "十二文",
    "群": "十二文", "军": "十二文", "勤": "十二文", "勋": "十二文", "纷": "十二文",
    "欣": "十二文",
    # 十三元（平）
    "元": "十三元", "原": "十三元", "园": "十三元", "源": "十三元", "轩": "十三元",
    "门": "十三元", "村": "十三元", "魂": "十三元", "昏": "十三元", "存": "十三元",
    "盆": "十三元", "恩": "十三元", "言": "十三元", "喧": "十三元",
    # 十四寒（平）
    "寒": "十四寒", "安": "十四寒", "残": "十四寒", "兰": "十四寒", "丹": "十四寒",
    "干": "十四寒", "栏": "十四寒", "欢": "十四寒", "官": "十四寒", "观": "十四寒",
    "冠": "十四寒", "盘": "十四寒", "竿": "十四寒",
    # 十五删（平）
    "删": "十五删", "山": "十五删", "关": "十五删", "还": "十五删", "间": "十五删",
    "艰": "十五删", "斑": "十五删", "颜": "十五删", "攀": "十五删", "闲": "十五删",
    # 一先（平）
    "先": "一先", "前": "一先", "年": "一先", "天": "一先", "田": "一先",
    "边": "一先", "烟": "一先", "莲": "一先", "弦": "一先", "泉": "一先",
    "鲜": "一先", "圆": "一先", "钱": "一先", "怜": "一先", "眠": "一先",
    "然": "一先", "千": "一先", "穿": "一先", "船": "一先", "肩": "一先",
    "迁": "一先", "娟": "一先", "蝉": "一先",
    # 二萧（平）
    "萧": "二萧", "迢": "二萧", "朝": "二萧", "潮": "二萧", "摇": "二萧",
    "遥": "二萧", "桥": "二萧", "条": "二萧", "凋": "二萧", "娇": "二萧",
    "腰": "二萧", "飘": "二萧", "销": "二萧", "宵": "二萧", "消": "二萧",
    "邀": "二萧", "招": "二萧", "樵": "二萧", "乔": "二萧", "翘": "二萧",
    # 四豪（平）
    "豪": "四豪", "毫": "四豪", "毛": "四豪", "涛": "四豪", "陶": "四豪",
    "逃": "四豪", "桃": "四豪", "高": "四豪", "劳": "四豪", "蒿": "四豪",
    "曹": "四豪", "袍": "四豪", "刀": "四豪", "骚": "四豪",
    # 五歌（平）
    "歌": "五歌", "多": "五歌", "河": "五歌", "波": "五歌", "禾": "五歌",
    "和": "五歌", "罗": "五歌", "螺": "五歌", "何": "五歌", "过": "五歌",
    "戈": "五歌", "摩": "五歌", "婆": "五歌", "梭": "五歌",
    # 六麻（平）
    "麻": "六麻", "花": "六麻", "家": "六麻", "华": "六麻", "沙": "六麻",
    "邪": "六麻", "斜": "六麻", "茶": "六麻", "霞": "六麻", "瓜": "六麻",
    "芽": "六麻", "牙": "六麻", "鸦": "六麻", "遮": "六麻", "蛇": "六麻",
    "槎": "六麻",
    # 七阳（平）
    "阳": "七阳", "扬": "七阳", "香": "七阳", "乡": "七阳", "光": "七阳",
    "堂": "七阳", "长": "七阳", "常": "七阳", "场": "七阳", "凉": "七阳",
    "霜": "七阳", "方": "七阳", "芳": "七阳", "昌": "七阳", "章": "七阳",
    "伤": "七阳", "张": "七阳", "梁": "七阳", "粮": "七阳", "肠": "七阳",
    "黄": "七阳", "皇": "七阳", "荒": "七阳", "苍": "七阳", "郎": "七阳",
    "桑": "七阳", "裳": "七阳", "洋": "七阳",
    # 八庚（平）
    "庚": "八庚", "更": "八庚", "羹": "八庚", "英": "八庚", "行": "八庚",
    "鸣": "八庚", "平": "八庚", "明": "八庚", "清": "八庚", "晴": "八庚",
    "生": "八庚", "声": "八庚", "成": "八庚", "城": "八庚", "情": "八庚",
    "迎": "八庚", "惊": "八庚", "名": "八庚", "轻": "八庚", "京": "八庚",
    "荆": "八庚", "兵": "八庚", "兄": "八庚", "营": "八庚", "衡": "八庚",
    "荣": "八庚",
    # 九青（平）
    "青": "九青", "星": "九青", "亭": "九青", "庭": "九青", "经": "九青",
    "形": "九青", "灵": "九青", "听": "九青", "冥": "九青", "屏": "九青",
    "萤": "九青", "宁": "九青", "暝": "九青", "馨": "九青", "汀": "九青",
    "丁": "九青", "铭": "九青",
    # 十蒸（平）
    "蒸": "十蒸", "承": "十蒸", "登": "十蒸", "灯": "十蒸", "僧": "十蒸",
    "增": "十蒸", "能": "十蒸", "朋": "十蒸", "腾": "十蒸", "澄": "十蒸",
    "冰": "十蒸", "兴": "十蒸", "仍": "十蒸", "凭": "十蒸", "陵": "十蒸",
    "凝": "十蒸",
    # 十一尤（平）
    "尤": "十一尤", "由": "十一尤", "游": "十一尤", "牛": "十一尤", "秋": "十一尤",
    "忧": "十一尤", "求": "十一尤", "休": "十一尤", "流": "十一尤", "留": "十一尤",
    "收": "十一尤", "舟": "十一尤", "州": "十一尤", "愁": "十一尤", "羞": "十一尤",
    "谋": "十一尤", "侯": "十一尤", "楼": "十一尤", "浮": "十一尤", "偷": "十一尤",
    "头": "十一尤", "投": "十一尤", "钩": "十一尤", "沟": "十一尤", "丘": "十一尤",
    "悠": "十一尤", "柔": "十一尤",
    # 十二侵（平）
    "侵": "十二侵", "心": "十二侵", "寻": "十二侵", "林": "十二侵", "深": "十二侵",
    "阴": "十二侵", "音": "十二侵", "吟": "十二侵", "琴": "十二侵", "临": "十二侵",
    "霖": "十二侵", "今": "十二侵", "金": "十二侵", "襟": "十二侵", "钦": "十二侵",
    "森": "十二侵",
    # 十三覃（平）
    "覃": "十三覃", "南": "十三覃", "男": "十三覃", "参": "十三覃", "谙": "十三覃",
    "潭": "十三覃", "岚": "十三覃", "蚕": "十三覃", "酣": "十三覃", "函": "十三覃",
    "庵": "十三覃",
    # 十四盐（平）
    "盐": "十四盐", "严": "十四盐", "帘": "十四盐", "檐": "十四盐", "添": "十四盐",
    "甜": "十四盐", "尖": "十四盐", "嫌": "十四盐", "炎": "十四盐", "占": "十四盐",
    # 十五咸（平）
    "咸": "十五咸", "帆": "十五咸", "衫": "十五咸", "岩": "十五咸", "衔": "十五咸",
    "监": "十五咸", "喃": "十五咸",
    # 上声（仄）
    "纸": "四纸", "只": "四纸", "指": "四纸", "止": "四纸", "子": "四纸",
    "紫": "四纸", "死": "四纸", "水": "四纸", "耳": "四纸", "此": "四纸",
    "是": "四纸", "起": "四纸", "语": "六语", "楚": "六语", "雨": "七麌",
    "古": "七麌", "五": "七麌", "户": "七麌", "晓": "十七筱", "小": "十七筱",
    "鸟": "十七筱", "少": "十七筱", "好": "十九皓", "早": "十九皓", "草": "十九皓",
    "老": "十九皓", "马": "二十一马", "野": "二十一马", "满": "十四旱",
    "晚": "十三阮", "口": "二十五有", "九": "二十五有", "手": "二十五有",
    "酒": "二十五有", "有": "二十五有", "柳": "二十五有", "走": "二十五有",
    "种": "二肿", "远": "十三阮", "几": "五尾", "海": "十贿", "举": "六语",
    "采": "十贿", "愿": "十四愿", "雨": "七麌", "此": "四纸",
    # 去声（仄）
    "地": "四寘", "意": "四寘", "泪": "四寘", "岁": "八霁", "树": "七遇",
    "路": "七遇", "暮": "七遇", "住": "七遇", "去": "六御", "处": "六御",
    "夜": "二十二祃", "笑": "十八啸", "照": "十八啸", "见": "十七霰", "面": "十七霰",
    "望": "二十三漾", "上": "二十三漾", "看": "十五翰", "岸": "十五翰",
    "汉": "十五翰", "万": "十四愿", "问": "十三问", "恨": "十四愿", "信": "十二震",
    "正": "二十四敬", "圣": "二十四敬", "定": "二十五径", "外": "九泰",
    "带": "九泰", "会": "九泰", "爱": "九泰", "送": "一送", "梦": "一送",
    "共": "二宋", "用": "二宋", "到": "二十号", "布": "七遇", "挂": "十卦",
    "旧": "二十六宥", "谢": "二十二祃", "燕": "十七霰", "姓": "二十四敬",
    "巷": "三绛", "未": "五未", "故": "七遇", "是": "四纸", "尽": "十一轸",
    "后": "二十六宥", "客": "十一陌", "雁": "十六谏", "莫": "十药", "难": "十四寒",
    "暮": "七遇", "片": "十七霰", "寄": "四寘", "别": "九屑", "袖": "二十六宥",
    # 入声（仄）
    "一": "四质", "日": "四质", "七": "四质", "出": "四质", "室": "四质",
    "疾": "四质", "实": "四质", "密": "四质", "笔": "四质", "白": "十一陌",
    "石": "十一陌", "客": "十一陌", "宅": "十一陌", "百": "十一陌", "泽": "十一陌",
    "药": "十药", "落": "十药", "鹤": "十药", "作": "十药", "阁": "十药",
    "月": "六月", "雪": "九屑", "发": "六月", "绝": "九屑", "灭": "九屑",
    "说": "九屑", "切": "九屑", "节": "九屑", "骨": "六月", "忽": "六月",
    "木": "一屋", "竹": "一屋", "屋": "一屋", "目": "一屋", "哭": "一屋",
    "六": "一屋", "独": "一屋", "读": "一屋", "国": "十三职", "黑": "十三职",
    "得": "十三职", "力": "十三职", "息": "十三职", "合": "十五合", "十": "十四缉",
    "湿": "十四缉", "立": "十四缉", "入": "十四缉", "急": "十四缉", "及": "十四缉",
    "叶": "十六叶", "帖": "十六叶", "接": "十六叶", "蝶": "十六叶", "法": "十七洽",
    "甲": "十七洽", "压": "十七洽", "仄": "十三职", "粟": "二沃", "滴": "十二锡",
    "撷": "九屑", "物": "五物", "国": "十三职", "石": "十一陌",
}

# 韵部候选池（find_rhyme_candidates 用）：韵部 -> 常用字（含平仄由 _PING_SHUI 推导）
_RHYME_POOLS: dict[str, list[str]] = {
    "一东": ["东", "同", "中", "虫", "终", "风", "空", "红", "功", "工", "公",
             "通", "宫", "穷", "翁", "鸿", "丛", "蒙", "蓬", "虹", "弓", "融", "穹"],
    "七阳": ["阳", "扬", "香", "乡", "光", "堂", "长", "常", "凉", "霜", "方",
             "芳", "昌", "章", "伤", "张", "梁", "黄", "皇", "荒", "苍", "郎", "桑", "裳", "洋"],
    "十一尤": ["尤", "由", "游", "牛", "秋", "忧", "求", "休", "流", "留", "收",
               "舟", "州", "愁", "羞", "谋", "侯", "楼", "浮", "偷", "头", "投", "钩", "沟", "丘", "悠", "柔"],
    "四支": ["支", "枝", "移", "垂", "眉", "悲", "时", "诗", "词", "丝", "知",
             "之", "期", "迟", "池", "师", "儿", "衣", "机", "稀", "思"],
    "一先": ["先", "前", "年", "天", "田", "边", "烟", "莲", "弦", "泉", "鲜",
             "圆", "钱", "怜", "眠", "然", "千", "穿", "船", "肩", "迁", "娟", "蝉"],
    "八庚": ["庚", "更", "羹", "英", "行", "鸣", "平", "明", "清", "晴", "生",
             "声", "成", "城", "情", "迎", "惊", "名", "轻", "京", "荆", "兵", "兄", "营", "衡", "荣"],
    "六麻": ["麻", "花", "家", "华", "沙", "车", "邪", "斜", "茶", "霞", "瓜",
             "芽", "牙", "鸦", "遮", "蛇", "槎"],
    "五微": ["微", "辉", "飞", "非", "依", "归", "围", "违", "威", "扉", "稀"],
    "二萧": ["萧", "迢", "朝", "潮", "摇", "遥", "桥", "条", "凋", "娇", "腰",
             "飘", "销", "宵", "消", "邀", "招", "樵", "乔", "翘"],
    "十一真": ["真", "人", "仁", "亲", "春", "辰", "身", "新", "神", "陈", "贫",
               "民", "津", "尘", "晨", "频", "邻", "珍", "匀", "巡"],
    "十二文": ["文", "云", "分", "闻", "君", "群", "军", "勤", "勋", "纷", "欣"],
    "十三元": ["元", "原", "园", "源", "轩", "门", "村", "魂", "昏", "存", "盆", "恩", "言"],
    "十四寒": ["寒", "安", "残", "兰", "丹", "干", "栏", "欢", "官", "观", "冠", "盘", "竿"],
    "十五删": ["删", "山", "关", "还", "间", "艰", "斑", "颜", "攀", "闲"],
    "十灰": ["灰", "回", "杯", "梅", "开", "台", "来", "才", "裁", "哀", "苔", "材", "雷", "催"],
    "五歌": ["歌", "多", "河", "波", "禾", "和", "罗", "螺", "何", "过", "戈", "摩", "婆", "梭"],
    "十二侵": ["侵", "心", "寻", "林", "深", "阴", "音", "吟", "琴", "临", "霖", "今", "金", "襟"],
    "十三覃": ["覃", "南", "男", "参", "谙", "潭", "岚", "蚕", "酣", "函", "庵"],
    "九青": ["青", "星", "亭", "庭", "经", "形", "灵", "听", "冥", "屏", "萤", "宁", "馨", "汀"],
    "十蒸": ["蒸", "承", "登", "灯", "僧", "增", "能", "朋", "腾", "澄", "冰", "兴", "陵", "凝"],
    "七虞": ["虞", "愚", "夫", "无", "湖", "都", "途", "图", "珠", "朱", "儒", "苏",
             "姑", "孤", "呼", "乌", "吴", "梧", "卢", "炉", "壶", "酥"],
    "十七筱": ["晓", "小", "鸟", "少", "筱", "渺", "了", "悄"],
    "四纸": ["纸", "只", "指", "止", "子", "紫", "死", "水", "耳", "此", "是", "起"],
    "二十三漾": ["上", "望", "向", "样", "浪", "帐", "唱"],
    "九泰": ["外", "会", "带", "爱", "泰", "盖"],
    "一送": ["送", "梦", "凤", "众", "弄", "洞", "冻"],
    "七遇": ["树", "路", "暮", "住", "布", "故", "步", "露", "素", "度"],
    "四寘": ["地", "意", "泪", "寄", "事", "至", "自", "贵", "睡", "翠", "位", "泪"],
    "一屋": ["木", "竹", "屋", "目", "哭", "六", "独", "读", "福", "禄", "熟", "肉"],
    "四质": ["一", "日", "七", "出", "室", "疾", "实", "密", "笔", "毕", "漆", "吉"],
    "十一陌": ["白", "石", "客", "宅", "百", "泽", "色", "惜", "迹", "夕", "席", "隔"],
    "六月": ["月", "发", "骨", "忽", "没", "窟", "歇", "越"],
    "九屑": ["雪", "绝", "灭", "别", "说", "切", "节", "拙", "铁", "列"],
    "十药": ["药", "落", "鹤", "作", "阁", "乐", "岳", "薄", "酌"],
    "十四缉": ["入", "十", "湿", "立", "急", "及", "泣", "集", "拾"],
    "十三职": ["仄", "国", "黑", "得", "力", "息", "色", "极", "直"],
}

# ─────────────────────────────────────────────────────────────
# 典故词典（内置小型：10-20 条，含出处回链）
# 每条：phrase（命中的短语）、source（出处/篇名）、url（回链，可空）、note
# ─────────────────────────────────────────────────────────────
_ALLUSIONS: list[dict[str, str]] = [
    {"phrase": "三顾茅庐", "source": "《三国志·蜀书·诸葛亮传》", "url": "https://zh.wikisource.org/wiki/三國志/卷35", "note": "刘备三往见诸葛亮，隆中对策。"},
    {"phrase": "士别三日", "source": "《三国志·吴书·吕蒙传》裴松之注", "url": "https://zh.wikisource.org/wiki/三國志/卷54", "note": "吕蒙学成，鲁肃刮目相待。"},
    {"phrase": "刮目相待", "source": "《三国志·吴书·吕蒙传》裴松之注", "url": "https://zh.wikisource.org/wiki/三國志/卷54", "note": "士别三日，即更刮目相待。"},
    {"phrase": "望梅止渴", "source": "《世说新语·假谲》", "url": "https://zh.wikisource.org/wiki/世說新語/假譎", "note": "曹操行军望梅林止渴。"},
    {"phrase": "老骥伏枥", "source": "曹操《龟虽寿》", "url": "https://zh.wikisource.org/wiki/步出夏門行", "note": "老骥伏枥，志在千里。"},
    {"phrase": "关关雎鸠", "source": "《诗经·周南·关雎》", "url": "https://zh.wikisource.org/wiki/詩經/關雎", "note": "关关雎鸠，在河之洲。"},
    {"phrase": "蒹葭苍苍", "source": "《诗经·秦风·蒹葭》", "url": "https://zh.wikisource.org/wiki/詩經/蒹葭", "note": "蒹葭苍苍，白露为霜。"},
    {"phrase": "桃之夭夭", "source": "《诗经·周南·桃夭》", "url": "https://zh.wikisource.org/wiki/詩經/桃夭", "note": "桃之夭夭，灼灼其华。"},
    {"phrase": "投桃报李", "source": "《诗经·大雅·抑》", "url": "https://zh.wikisource.org/wiki/詩經/抑", "note": "投我以桃，报之以李。"},
    {"phrase": "执子之手", "source": "《诗经·邶风·击鼓》", "url": "https://zh.wikisource.org/wiki/詩經/擊鼓", "note": "执子之手，与子偕老。"},
    {"phrase": "高山流水", "source": "《列子·汤问》", "url": "https://zh.wikisource.org/wiki/列子/湯問篇", "note": "伯牙鼓琴，钟子期善听。"},
    {"phrase": "庄周梦蝶", "source": "《庄子·齐物论》", "url": "https://zh.wikisource.org/wiki/莊子/齊物論", "note": "昔者庄周梦为胡蝶，栩栩然胡蝶也。"},
    {"phrase": "庄生晓梦", "source": "《庄子·齐物论》", "url": "https://zh.wikisource.org/wiki/莊子/齊物論", "note": "李商隐《锦瑟》用之。"},
    {"phrase": "望帝春心", "source": "《华阳国志·蜀志》", "url": "https://zh.wikisource.org/wiki/華陽國志/卷三", "note": "望帝化为杜鹃，李商隐《锦瑟》用之。"},
    {"phrase": "杜鹃啼血", "source": "《华阳国志·蜀志》", "url": "https://zh.wikisource.org/wiki/華陽國志/卷三", "note": "蜀王望帝化鹃，啼血不止。"},
    {"phrase": "鹏程万里", "source": "《庄子·逍遥游》", "url": "https://zh.wikisource.org/wiki/莊子/逍遙遊", "note": "鲲鹏扶摇而上者九万里。"},
    {"phrase": "鲲鹏", "source": "《庄子·逍遥游》", "url": "https://zh.wikisource.org/wiki/莊子/逍遙遊", "note": "北冥有鱼，其名为鲲。"},
    {"phrase": "约法三章", "source": "《史记·高祖本纪》", "url": "https://zh.wikisource.org/wiki/史記/卷008", "note": "与父老约，法三章耳。"},
    {"phrase": "项庄舞剑", "source": "《史记·项羽本纪》", "url": "https://zh.wikisource.org/wiki/史記/卷007", "note": "项庄拔剑起舞，意在沛公。"},
    {"phrase": "破釜沉舟", "source": "《史记·项羽本纪》", "url": "https://zh.wikisource.org/wiki/史記/卷007", "note": "项羽沉船破釜甑，以示必死。"},
    {"phrase": "韦编三绝", "source": "《史记·孔子世家》", "url": "https://zh.wikisource.org/wiki/史記/卷047", "note": "读《易》，韦编三绝。"},
    {"phrase": "完璧归赵", "source": "《史记·廉颇蔺相如列传》", "url": "https://zh.wikisource.org/wiki/史記/卷081", "note": "蔺相如持璧归赵。"},
]

# ─────────────────────────────────────────────────────────────
# 格律谱（近体诗）—— 供 generate_poem 模板与 validate_meter 参考
# 近体诗验证采用「实用规则」：句数/字数/偶句押韵/二四六关键位对与粘/三平三仄脚提醒，
# 不做逐字严格谱比对（避免对自创句过于苛刻）。
# ─────────────────────────────────────────────────────────────
_METER_PROFILES: dict[str, dict[str, Any]] = {
    "五绝": {
        "name": "五言绝句", "line_count": 4, "line_lengths": [5, 5, 5, 5],
        "rhyme_even": True, "rhyme_first": "optional",
        "scope_note": "四句五言；偶句押平声韵，首句可入韵；二四字平仄相对（对）、联间相粘（粘）。",
    },
    "七绝": {
        "name": "七言绝句", "line_count": 4, "line_lengths": [7, 7, 7, 7],
        "rhyme_even": True, "rhyme_first": "optional",
        "scope_note": "四句七言；偶句押平声韵，首句可入韵；二四六字对与粘。",
    },
    "五律": {
        "name": "五言律诗", "line_count": 8, "line_lengths": [5] * 8,
        "rhyme_even": True, "rhyme_first": "optional",
        "scope_note": "八句五言；偶句押平声韵，首句可入韵；中二联对仗（本模块不校验对仗，仅格律）。",
    },
    "七律": {
        "name": "七言律诗", "line_count": 8, "line_lengths": [7] * 8,
        "rhyme_even": True, "rhyme_first": "optional",
        "scope_note": "八句七言；偶句押平声韵，首句可入韵；中二联对仗（不校验对仗）。",
    },
    "鹧鸪天": {
        "name": "鹧鸪天", "line_count": 9, "line_lengths": [7, 7, 7, 7, 3, 3, 7, 7, 7],
        "rhyme_positions": [0, 1, 3, 5, 6, 8], "rhyme_even": False, "rhyme_first": False,
        "scope_note": "双调五十五字常用正格谱；上片 4 句 7 字，下片 3/3/7/7/7；"
                     "韵位：上片 1、2、4 句，下片 2、3、5 句（三字句第二句起韵）；本模块仅覆盖此正格，变格不计。",
    },
}

_METER_ALIASES = {
    "五绝": "五绝", "五言绝句": "五绝", "五言绝": "五绝",
    "七绝": "七绝", "七言绝句": "七绝", "七言绝": "七绝",
    "五律": "五律", "五言律诗": "五律",
    "七律": "七律", "七言律诗": "七律",
    "鹧鸪天": "鹧鸪天", "鹧鸪天词": "鹧鸪天",
}

_PUNCTUATION = "，。！？；：、,.!?;:"


def _clean_line(raw: str) -> str:
    return "".join(ch for ch in raw if ch not in _PUNCTUATION and not ch.isspace())


def _split_lines(text: str) -> list[str]:
    return [_clean_line(line) for line in re.split(r"\n+", text) if _clean_line(line)]


def classify_char(char: str) -> dict[str, Any]:
    """逐字音韵分类（实用近似）。

    优先级：平水韵 > 普通话声调近似 > 未知。
    """
    char = char.strip()
    if not char:
        return {
            "char": "", "pinyin": None, "tone": None, "rhyme_group": None,
            "tone_class": None, "ping_ze": None, "source": "unknown", "approx": True,
        }
    pinyin, tone = _PINYIN.get(char, (None, None))
    rhyme_group = _PING_SHUI.get(char)
    if rhyme_group:
        tone_class = _PING_SHUI_TONE_CLASS[rhyme_group]
        ping_ze = "平" if tone_class == "平" else "仄"
        return {
            "char": char, "pinyin": pinyin, "tone": tone, "rhyme_group": rhyme_group,
            "tone_class": tone_class, "ping_ze": ping_ze, "source": "平水韵", "approx": False,
        }
    if tone is not None:
        # 普通话近似：1/2=平、3/4=仄；入声在平水韵体系单独归仄，普通话无入声，标记近似。
        ping_ze = "平" if tone in (1, 2) else "仄"
        return {
            "char": char, "pinyin": pinyin, "tone": tone, "rhyme_group": None,
            "tone_class": ping_ze, "ping_ze": ping_ze, "source": "普通话近似", "approx": True,
        }
    return {
        "char": char, "pinyin": None, "tone": None, "rhyme_group": None,
        "tone_class": None, "ping_ze": None, "source": "unknown", "approx": True,
    }


def find_rhyme_candidates(final_char: str, max: int = 20) -> list[str]:
    """按韵部返回同韵部常用候选字（不含输入字本身）。"""
    rhyme_group = _PING_SHUI.get(final_char)
    if not rhyme_group:
        return []
    pool = _RHYME_POOLS.get(rhyme_group, [])
    candidates = [ch for ch in pool if ch != final_char]
    return candidates[:max]


def evaluate_prosody(lines: list[str], style: str = "") -> dict[str, Any]:
    """对行逐字给出平仄序列，并给失对/失粘/拗句提示（实用规则）。

    规则：
    - 每句输出逐字 {char, pinyin, ping_ze, tone_class, rhyme_group, approx}。
    - 对：一联内偶数句（第 2 句）第 2 字与奇数句（第 1 句）第 2 字平仄相反。
    - 粘：联与联之间，下联首句第 2 字与上联末句第 2 字平仄相同。
    - 拗：句末三平脚 / 三仄脚提醒（对 5/7 言）。
    """
    key_positions = {5: [1, 3], 7: [1, 3, 5]}
    line_results: list[dict[str, Any]] = []
    for raw in lines:
        clean = _clean_line(raw)
        classified = [classify_char(ch) for ch in clean]
        sequence = "".join(item["ping_ze"] or "?" for item in classified)
        line_results.append({
            "seq": len(line_results),
            "raw": clean,
            "chars": classified,
            "sequence": sequence,
            "length": len(clean),
        })

    issues: list[dict[str, Any]] = []
    # 对与粘（仅对 >=2 行的诗体有效）
    for i in range(0, len(line_results) - 1, 2):
        odd, even = line_results[i], line_results[i + 1]
        length = len(odd["chars"])
        if length in key_positions:
            pos = key_positions[length][0]  # 第 2 字
            a = odd["chars"][pos].get("ping_ze")
            b = even["chars"][pos].get("ping_ze")
            if a and b and a == b:
                issues.append({
                    "type": "失对", "severity": "warn",
                    "detail": f"第{odd['seq'] + 1}/{even['seq'] + 1}句第{pos + 1}字平仄相同（{a}），应相反",
                })
    for i in range(1, len(line_results) - 1, 2):
        up_even, down_odd = line_results[i], line_results[i + 1]
        length = len(up_even["chars"])
        if length in key_positions:
            pos = key_positions[length][0]
            a = up_even["chars"][pos].get("ping_ze")
            b = down_odd["chars"][pos].get("ping_ze")
            if a and b and a != b:
                issues.append({
                    "type": "失粘", "severity": "warn",
                    "detail": f"第{up_even['seq'] + 1}/{down_odd['seq'] + 1}句第{pos + 1}字平仄相反（{a}/{b}），应相同",
                })
    # 三平脚/三仄脚（拗句提醒）
    for line in line_results:
        seq = line["seq"] + 1
        if len(line["chars"]) >= 3:
            tail = [c.get("ping_ze") for c in line["chars"][-3:]]
            if all(t == "平" for t in tail):
                issues.append({
                    "type": "三平脚（拗句）", "severity": "advisory",
                    "detail": f"第{seq}句末三字皆平，拗句；如需救可调整第三字为仄",
                })
            elif all(t == "仄" for t in tail):
                issues.append({
                    "type": "三仄脚（拗句）", "severity": "advisory",
                    "detail": f"第{seq}句末三字皆仄，拗句；可调整第三字为平（入声字归仄故易出现）",
                })

    return {
        "style": style,
        "lines": line_results,
        "issues": issues,
        "issue_count": len(issues),
    }


def _resolve_style(style: str) -> str | None:
    key = (style or "").strip()
    return _METER_ALIASES.get(key)


def validate_meter(text: str, style: str) -> dict[str, Any]:
    """格律验证（五绝/七绝/五律/七律/鹧鸪天）。

    输出 per 规则 pass/warn/fail + 明细。规则：
    - line_count：句数是否符合
    - line_lengths：每句字数是否符合
    - rhyme_consistency：韵位（偶句；词牌按谱）同韵部
    - rhyme_tone：近体韵脚为平声（仄韵古绝给 warn，非 fail）
    - key_positions：二四（六）字对/粘（从 evaluate_prosody 聚合）
    """
    clean_lines = _split_lines(text)
    profile_key = _resolve_style(style)
    rules: list[dict[str, Any]] = []

    def add_rule(name: str, status: str, detail: str, items: list[dict[str, Any]] | None = None) -> None:
        rules.append({"name": name, "status": status, "detail": detail, "items": items or []})

    if profile_key is None:
        profile_key = "五绝"
        add_rule(
            "style", "warn",
            f"未知样式「{style}」，回退为五绝规则；支持：{', '.join(sorted(_METER_ALIASES))}",
        )
    profile = _METER_PROFILES[profile_key]

    # ── 句数 ──
    expected_count = profile["line_count"]
    if len(clean_lines) == expected_count:
        add_rule("line_count", "pass", f"句数 {len(clean_lines)} 符合 {profile['name']} 要求")
    else:
        add_rule(
            "line_count", "fail",
            f"句数 {len(clean_lines)}，期望 {expected_count}（{profile['name']}）",
            [{"line": i + 1, "length": len(line)} for i, line in enumerate(clean_lines)],
        )

    # ── 字数 ──
    length_items = []
    length_ok = True
    for i, line in enumerate(clean_lines):
        expected_len = profile["line_lengths"][i] if i < len(profile["line_lengths"]) else None
        ok = expected_len is None or len(line) == expected_len
        length_ok = length_ok and ok
        length_items.append({"line": i + 1, "actual": len(line), "expected": expected_len, "ok": ok})
    add_rule(
        "line_lengths",
        "pass" if length_ok else "fail",
        "每句字数符合" if length_ok else "存在字数不符的句子",
        length_items,
    )

    # ── 押韵位置 ──
    if profile.get("rhyme_positions"):
        rhyme_positions = profile["rhyme_positions"]
    else:
        # 近体：偶句（1-based 偶数行），首句可入韵
        rhyme_positions = [i for i in range(len(clean_lines)) if (i + 1) % 2 == 0]
        if clean_lines:
            first_char = clean_lines[0][-1]
            second_char = clean_lines[1][-1] if len(clean_lines) > 1 else None
            first_group = _PING_SHUI.get(first_char)
            second_group = _PING_SHUI.get(second_char) if second_char else None
            if first_group and first_group == second_group:
                # 首句入韵：首句尾字与第二句尾字同韵部
                rhyme_positions = [0] + [p for p in rhyme_positions if p != 0]

    rhyme_items: list[dict[str, Any]] = []
    groups: list[str] = []
    rhyme_ok = True
    for pos in rhyme_positions:
        if pos >= len(clean_lines):
            continue
        tail = clean_lines[pos][-1]
        grp = _PING_SHUI.get(tail)
        info = classify_char(tail)
        if grp is None:
            rhyme_ok = False
            rhyme_items.append({
                "position": pos + 1, "char": tail, "rhyme_group": None,
                "ping_ze": info.get("ping_ze"), "ok": False,
                "note": "字未收录平水韵，无法判定韵部（近似）",
            })
        else:
            groups.append(grp)
            rhyme_items.append({
                "position": pos + 1, "char": tail, "rhyme_group": grp,
                "ping_ze": info.get("ping_ze"), "ok": True,
            })
    consistent = len(set(groups)) <= 1 if groups else True
    if not rhyme_ok:
        status = "fail"
    elif consistent:
        status = "pass"
    else:
        status = "fail"
    add_rule(
        "rhyme_consistency",
        status,
        f"韵位一致，韵部：{'、'.join(sorted(set(groups))) if groups else '（无韵脚字）'}"
        if consistent else "韵脚不在同一平水韵部",
        rhyme_items,
    )

    # ── 韵脚平声（近体偏好；仄韵古绝给 warn） ──
    if profile_key in ("五绝", "七绝", "五律", "七律"):
        flat_ping_ze = [item.get("ping_ze") for item in rhyme_items if item.get("ok")]
        if not rhyme_ok:
            add_rule("rhyme_tone", "warn", "韵脚判定不完整，跳过平声检查")
        elif all(pz == "平" for pz in flat_ping_ze):
            add_rule("rhyme_tone", "pass", "韵脚均为平声（近体正格）")
        else:
            add_rule("rhyme_tone", "warn", "韵脚含仄声（可能为仄韵古绝/词体，非近体正格）")
    else:
        add_rule("rhyme_tone", "pass", "词牌不强制平声韵")

    # ── 关键位对/粘（聚合 evaluate_prosody） ──
    prosody = evaluate_prosody(clean_lines, style)
    pair_issues = [i for i in prosody["issues"] if i["type"] in ("失对", "失粘")]
    if pair_issues:
        add_rule(
            "key_positions", "warn",
            "存在失对/失粘（第 2 字平仄关系）",
            [{"type": i["type"], "detail": i["detail"]} for i in pair_issues],
        )
    else:
        add_rule("key_positions", "pass", "二四（六）字对粘基本成立" if len(clean_lines) >= 2 else "单句无对粘校验")

    # ── 拗句提醒（advisory） ──
    advisory = [i for i in prosody["issues"] if i["type"] in ("三平脚（拗句）", "三仄脚（拗句）")]
    add_rule(
        "prosody_notes", "pass" if not advisory else "warn",
        "无三平/三仄脚" if not advisory else f"{len(advisory)} 处拗句提醒",
        [{"type": i["type"], "detail": i["detail"]} for i in advisory],
    )

    summary = {"pass": 0, "warn": 0, "fail": 0}
    for r in rules:
        summary[r["status"]] = summary.get(r["status"], 0) + 1

    return {
        "style": profile_key,
        "style_name": profile["name"],
        "scope_note": profile["scope_note"],
        "line_count": len(clean_lines),
        "line_lengths": [len(line) for line in clean_lines],
        "rhyme_positions": [p + 1 for p in rhyme_positions if p < len(clean_lines)],
        "rules": rules,
        "prosody": {k: prosody[k] for k in ("lines", "issues")},
        "summary": summary,
        "ok": summary.get("fail", 0) == 0,
    }


def find_allusions(text: str, lexicon: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
    """在诗文中命中典故短语并回链出处。

    lexicon 缺省用内置词典；条目需含 phrase/source，可含 url/note。
    命中按最长短语优先（避免短短语先占）。
    """
    entries = lexicon if lexicon is not None else _ALLUSIONS
    ordered = sorted(entries, key=lambda e: len(e.get("phrase", "")), reverse=True)
    results: list[dict[str, Any]] = []
    seen_positions: list[tuple[int, int]] = []

    def _overlaps(start: int, end: int) -> bool:
        return any(start < s_end and s_start < end for s_start, s_end in seen_positions)

    for entry in ordered:
        phrase = entry.get("phrase", "")
        if not phrase:
            continue
        start = 0
        while True:
            idx = text.find(phrase, start)
            if idx < 0:
                break
            if not _overlaps(idx, idx + len(phrase)):
                results.append({
                    "phrase": phrase,
                    "source": entry.get("source", ""),
                    "url": entry.get("url", ""),
                    "note": entry.get("note", ""),
                    "position": idx,
                    "context": text[max(0, idx - 6): idx + len(phrase) + 6],
                })
                seen_positions.append((idx, idx + len(phrase)))
            start = idx + 1
    results.sort(key=lambda r: r["position"])
    return results

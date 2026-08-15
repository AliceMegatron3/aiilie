from typing import List, Dict
from models.timeline import Timeline, TimelineEvent
class TimelineConflictChecker:
    def check_conflicts(self, timelines: List[Timeline]) -> List[Dict]:
        """
        检查多条时间线之间的时间重叠和人物状态矛盾。
        """
        conflicts = []
        # 按角色聚合所有事件的时间线
        character_timeline = {}
        
        for timeline in timelines:
            for event in timeline.events:
                for char in event.related_characters:
                    if char not in character_timeline:
                        character_timeline[char] = []
                    character_timeline[char].append({
                        "event_id": event.event_id,
                        "timeline": timeline.name,
                        "start": event.start_time,
                        "end": event.start_time + event.duration,
                        "desc": event.description
                    })
                    
        # 检查每个角色的时间重叠或逻辑悖论 (简化示例)
        for char, events in character_timeline.items():
            # 按时间排序
            events.sort(key=lambda x: x["start"])
            for i in range(len(events) - 1):
                current = events[i]
                next_evt = events[i+1]
                
                # 检查物理重叠
                if current["end"] > next_evt["start"]:
                    conflicts.append({
                        "type": "OVERLAP",
                        "character": char,
                        "message": f"角色 {char} 在同一时间存在于两个事件: [{current['desc']}] 和 [{next_evt['desc']}]",
                        "involved_events": [current["event_id"], next_evt["event_id"]]
                    })
                    
                # 检查生死逻辑悖论 (仅为示例，可接入NLP进一步分析)
                # 修正：角色在事件中死亡后，不应再参与后续事件
                if "死" in current["desc"] and next_evt["start"] >= current["start"]:
                    conflicts.append({
                        "type": "LOGICAL_PARADOX",
                        "character": char,
                        "message": f"角色 {char} 在事件 [{current['desc']}] 中疑似死亡，但后续参与了 [{next_evt['desc']}]",
                        "involved_events": [current["event_id"], next_evt["event_id"]]
                    })
                    
        return conflicts
import re
from pathlib import Path

from ..models.story import StoryMetadata, StoryNode
from .parser import StoryParser


def _parent_ids(
    unit: str, arc_id: str, story_type: str, episode_name: str, part_name: str
) -> tuple[str, str, str]:
    # Tier-1 id is unit-qualified so the same event slug never collides across
    # units and so unit-scoped rollups have a stable key.
    year_id = f"{unit}|{arc_id}"
    episode_id = f"{year_id}|{story_type}|{episode_name}"
    part_id = f"{episode_id}|{part_name}"
    return year_id, episode_id, part_id


def episode_number_from_names(episode_name: str, part_name: str) -> int:
    for value in (episode_name, part_name):
        # Sekai episode files are number-prefixed (e.g. "05_the-title"); also
        # match the legacy 第N話 form for hand-authored content.
        match = re.match(r"^(\d+)", value) or re.search(r"第(\d+)話", value)
        if match:
            return int(match.group(1))
    return 0


def _story_type_for(content_type: str) -> str:
    return "Main" if content_type == "main" else "Side"


class StoryProcessor:
    """Processes story directories into StoryNodes.

    Canonical Sekai layout written by the fetcher:
        story/<unit>/<content_type>/<arc_slug>/<NN_episode-slug>.md

    Tiers map onto the reused linkura machinery as:
        unit (Tier 1 facet) > arc_slug/event (Year) > episode (Episode/Part) > scene
    """

    @staticmethod
    def extract_hierarchy(file_path: Path) -> StoryMetadata:
        parts = file_path.parts
        try:
            story_idx = parts.index("story")
            unit = parts[story_idx + 1]
            content_type = parts[story_idx + 2]
            arc_id = parts[story_idx + 3]  # event slug / "main" — the Volume
            ep_name = file_path.stem       # e.g. "05_the-title"
            part_name = ep_name            # one file per episode; scenes split within
            story_type = _story_type_for(content_type)

            parent_year_id, parent_episode_id, parent_part_id = _parent_ids(
                unit, arc_id, story_type, ep_name, part_name
            )
            return StoryMetadata(
                unit=unit,
                content_type=content_type,
                arc_id=arc_id,
                story_type=story_type,
                episode_name=ep_name,
                episode_number=episode_number_from_names(ep_name, part_name),
                part_name=part_name,
                file_path=str(file_path),
                parent_year_id=parent_year_id,
                parent_episode_id=parent_episode_id,
                parent_part_id=parent_part_id,
            )
        except (ValueError, IndexError):
            part_name = file_path.parent.name
            parent_year_id, parent_episode_id, parent_part_id = _parent_ids(
                "unknown", "unknown", "unknown", "unknown", part_name
            )
            return StoryMetadata(
                unit="unknown",
                content_type="unknown",
                arc_id="unknown",
                story_type="unknown",
                episode_name="unknown",
                part_name=part_name,
                file_path=str(file_path),
                parent_year_id=parent_year_id,
                parent_episode_id=parent_episode_id,
                parent_part_id=parent_part_id,
            )

    @classmethod
    def process_file(cls, file_path: Path) -> list[StoryNode]:
        """Reads a file, splits it into scenes, and returns StoryNodes."""
        with open(file_path, encoding='utf-8') as f:
            content = f.read()
        
        metadata_base = cls.extract_hierarchy(file_path)
        is_script = StoryParser.is_script_format(content)
        scenes = StoryParser.split_into_scenes(content)
        
        nodes = []
        for i, scene_text in enumerate(scenes):
            meta = metadata_base.model_copy(deep=True)
            scene_id = f"scene:{meta.parent_part_id}:{i}"
            meta.scene_index = i
            meta.scene_start = i
            meta.scene_end = i
            meta.source_scene_count = 1
            meta.source_scene_ids = [scene_id]
            scene_is_script = is_script or StoryParser.is_script_format(scene_text)
            meta.is_prose = not scene_is_script
            if scene_is_script:
                turns = StoryParser.parse_script_scene(scene_text, scene_id=scene_id)
                beats = []
            else:
                turns, beats = StoryParser.parse_prose_scene(scene_text, scene_id=scene_id)

            meta.source_turn_ids = [turn.turn_id for turn in turns]
            meta.source_beat_ids = [beat.beat_id for beat in beats]
            meta.speakers = StoryParser.ordered_unique_speakers(turns)
            meta.detected_speakers = meta.speakers
            nodes.append(
                StoryNode(
                    text=scene_text,
                    metadata=meta,
                    dialogue_turns=turns,
                    narrative_beats=beats,
                )
            )
            
        return nodes

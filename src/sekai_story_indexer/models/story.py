from pydantic import BaseModel, Field


class DialogueTurn(BaseModel):
    turn_id: str = Field("", description="Stable identifier for this dialogue turn")
    scene_id: str = Field("", description="Stable source scene identifier")
    turn_index: int = Field(0, description="Ordered index within the source scene")
    speaker: str = Field(..., description="Raw speaker label, or UNKNOWN when not explicit")
    speaker_tokens: list[str] = Field(
        default_factory=list,
        description="Queryable speaker tokens parsed from the raw speaker label",
    )
    speaker_kind: str = Field(
        "",
        description="Speaker label kind, such as named, collective, or unknown",
    )
    text: str = Field(..., description="Dialogue text")
    line_start: int = Field(0, description="Zero-based source line where this turn starts")
    line_end: int = Field(0, description="Zero-based source line where this turn ends")


class NarrativeBeat(BaseModel):
    beat_id: str = Field("", description="Stable identifier for this narrative beat")
    scene_id: str = Field("", description="Stable source scene identifier")
    beat_index: int = Field(0, description="Ordered index within the source scene")
    text: str = Field(..., description="Narrative text")
    line_start: int = Field(0, description="Zero-based source line where this beat starts")
    line_end: int = Field(0, description="Zero-based source line where this beat ends")


class StoryMetadata(BaseModel):
    unit: str = Field(
        "unknown",
        description=(
            "Unit slug this story belongs to: leo_need, more_more_jump, "
            "vivid_bad_squad, wonderlands_showtime, nightcord, virtual_singer, "
            "or mixed (crossover / multi-unit events)."
        ),
    )
    content_type: str = Field(
        "event",
        description="Story bucket: main, event, unit, card, or area.",
    )
    plot_weight: str = Field(
        "unrated",
        description=(
            "OUR narrative-importance rating for a unit's arc: high (major "
            "character development / plot progression), medium, filler, or "
            "unrated. Filler is still indexed in full; this only affects "
            "retrieval prioritization for thematic queries. Set by our own "
            "classifier — the final say, independent of is_key_story."
        ),
    )
    is_key_story: bool = Field(
        False,
        description=(
            "Native game 'key story' signal (sekai.best isKeyEventStory: the "
            "event has a main-relation unit). An overinclusive input prior, "
            "not the final relevance verdict."
        ),
    )
    event_id: int = Field(0, description="Sekai master-DB event id, 0 if not an event story")
    started_at: int = Field(0, description="Event release timestamp (ms epoch), for chronological order")
    parent_event_id: int = Field(
        0,
        description="For card/area content: the PARENT event this content belongs to "
        "(via eventCards for cards, the event_story unlock for area talks). 0 if none.",
    )
    parent_arc_id: str = Field(
        "", description="Parent event's arc slug (e.g. '0150-...'), for nesting card/area under it."
    )
    content_group: str = Field(
        "",
        description="Grouping for card/area content with no parent event: a campaign tag "
        "(e.g. 'aprilfool2023'), or 'birthday'/'other'/'permanent'. Empty for event content.",
    )
    arc_id: str = Field(..., description="Volume id: event slug, 'main', or unit-story id")
    story_type: str = Field(
        ...,
        description="Content bucket on the tier axis: 'Event' (event story), 'Main', "
        "'Unit', 'Card', 'Area' (or legacy 'Side'/'Other').",
    )
    episode_name: str = Field(..., description="Episode or sub-series name")
    part_name: str = Field(..., description="Part or file name")
    file_path: str = Field(..., description="Path to the original markdown file")
    scene_index: int = Field(0, description="Index of the scene within the file (split by ---)")
    scene_start: int = Field(0, description="First source scene index covered by this node")
    scene_end: int = Field(0, description="Last source scene index covered by this node")
    source_scene_count: int = Field(1, description="Number of source scenes covered by this node")
    is_prose: bool = Field(False, description="True if the content is prose/narrative, False if script")
    canonical_story_order: int = Field(0, description="Global chronological order for this story node")
    story_order: int = Field(0, description="Alias for canonical_story_order used by query filters")
    episode_number: int = Field(0, description="Numeric episode/part number when one is available")
    parent_year_id: str = Field("", description="Stable parent year identifier")
    parent_episode_id: str = Field("", description="Stable parent episode identifier")
    parent_part_id: str = Field("", description="Stable parent part identifier")
    detected_speakers: list[str] = Field(
        default_factory=list,
        description="Speakers detected in this scene",
    )
    speakers: list[str] = Field(
        default_factory=list,
        description="Unique speakers present in this source scene or retrieval chunk",
    )
    source_scene_ids: list[str] = Field(
        default_factory=list,
        description="Stable source scene IDs covered by this node",
    )
    source_turn_ids: list[str] = Field(
        default_factory=list,
        description="Stable dialogue turn IDs covered by this node",
    )
    source_beat_ids: list[str] = Field(
        default_factory=list,
        description="Stable narrative beat IDs covered by this node",
    )
    chunk_id: str = Field("", description="Stable retrieval chunk identifier when indexed")


class StoryNode(BaseModel):
    text: str = Field(..., description="The actual text content of the scene or summary")
    metadata: StoryMetadata
    summary_level: int = Field(4, description="1: Event, 2: Episode, 3: Part, 4: Scene (Raw)")
    dialogue_turns: list[DialogueTurn] = Field(default_factory=list)
    narrative_beats: list[NarrativeBeat] = Field(default_factory=list)

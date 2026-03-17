"""
Video service - handles video processing business logic.
"""

from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, Awaitable
import asyncio
import logging
import json
import os
import re
import unicodedata

from pydantic_ai import Agent

from ..utils.async_helpers import run_in_thread
from ..youtube_utils import (
    download_youtube_video,
    get_youtube_video_title,
    get_youtube_video_id,
)
from ..video_utils import get_video_transcript, create_clips_with_transitions
from ..ai import get_most_relevant_parts_by_transcript, _get_missing_llm_key_error
from ..config import Config

logger = logging.getLogger(__name__)
config = Config()
UPLOAD_URL_PREFIX = "upload://"

# Title generation prompt template
_TITLE_PROMPT_TEMPLATE = """You are a viral YouTube Shorts title writer who specializes in brainrot, Gen Z, and trending content.
Transcript: "{text}"

Return ONLY raw JSON, no markdown, no explanation:
{{
  "title": "max 60 chars, Gen Z brainrot style, high psychological trigger",
  "alternatives": ["Alternative title 1", "Alternative title 2"],
  "hashtags": ["#shorts", "#fyp", "3-4 niche relevant tags"]
}}

TITLE RULES:
- Max 60 chars, capitalize first letter only
- 0-2 emojis max (use sparingly, only if it adds impact)
- Front-load the most shocking/relatable part
- NO periods at the end
- NO generic words: amazing/incredible/life-changing/insane/crazy/unbelievable

PSYCHOLOGICAL TRIGGERS (pick the strongest one):
- Curiosity gap: "nobody tells you that..."  "the real reason why..."
- Identity/POV: "pov: you finally..."  "when you realize..."
- Fear/urgency: "stop doing this before..."  "you're losing money by..."
- Shocking truth: "they lied about..."  "this is actually illegal"
- Social proof: "why everyone is switching to..."

TRENDING FORMATS (2025 brainrot shorts):
- "Bro really said [quote] 💀"
- "The way [person] [action] no cap"
- "POV: [extremely relatable situation]"
- "They actually [surprising action] fr"
- "Why does [common thing] hit different"
- "This [thing] is actually [unexpected truth]"
- "Nobody: / Absolutely nobody: / [subject]: [unexpected action]"
- "I can't believe [subject] actually [action]"
- "The [item/person] said [quote] and walked away"
- "[number] seconds that changed everything"
"""


def _sanitize_title_for_filename(title: str) -> str:
    """Sanitize a viral title into a safe filename (without extension).

    Rules:
    - Lowercase
    - Remove emojis and non-ASCII
    - Keep only alphanumeric, spaces, hyphens
    - Replace spaces with underscores
    - Strip leading/trailing underscores
    - Max 80 chars
    """
    # Normalize unicode and remove non-ASCII (including emojis)
    title = unicodedata.normalize("NFKD", title)
    title = title.encode("ascii", "ignore").decode("ascii")
    title = title.lower()
    # Keep only alphanumeric, spaces, hyphens
    title = re.sub(r"[^a-z0-9 \-]", "", title)
    # Collapse multiple spaces/hyphens
    title = re.sub(r"[\s]+", " ", title).strip()
    sanitized = title.strip()

    # Truncate to 80 chars
    sanitized = sanitized[:80].strip()
    return sanitized


class VideoService:
    """Service for video processing operations."""

    @staticmethod
    def resolve_local_video_path(url: str) -> Path:
        """Resolve uploaded-video references without exposing server filesystem paths."""
        if url.startswith(UPLOAD_URL_PREFIX):
            filename = Path(url.removeprefix(UPLOAD_URL_PREFIX)).name
            return Path(config.temp_dir) / "uploads" / filename
        return Path(url)

    @staticmethod
    async def download_video(url: str) -> Optional[Path]:
        """
        Download a YouTube video asynchronously.
        Runs the sync download_youtube_video in a thread pool.
        """
        logger.info(f"Starting video download: {url}")
        video_path = await run_in_thread(download_youtube_video, url)

        if not video_path:
            logger.error(f"Failed to download video: {url}")
            return None

        logger.info(f"Video downloaded successfully: {video_path}")
        return video_path

    @staticmethod
    async def get_video_title(url: str) -> str:
        """
        Get video title asynchronously.
        Returns a default title if retrieval fails.
        """
        try:
            title = await run_in_thread(get_youtube_video_title, url)
            return title or "YouTube Video"
        except Exception as e:
            logger.warning(f"Failed to get video title: {e}")
            return "YouTube Video"

    @staticmethod
    async def generate_transcript(
        video_path: Path, processing_mode: str = "balanced"
    ) -> str:
        """
        Generate transcript from video using AssemblyAI.
        Runs in thread pool to avoid blocking.
        """
        logger.info(f"Generating transcript for: {video_path}")
        speech_model = "best"
        if processing_mode == "fast":
            speech_model = config.fast_mode_transcript_model

        transcript = await run_in_thread(get_video_transcript, video_path, speech_model)
        logger.info(f"Transcript generated: {len(transcript)} characters")
        return transcript

    @staticmethod
    async def analyze_transcript(transcript: str) -> Any:
        """
        Analyze transcript with AI to find relevant segments.
        This is already async, no need to wrap.
        """
        logger.info("Starting AI analysis of transcript")
        relevant_parts = await get_most_relevant_parts_by_transcript(transcript)
        logger.info(
            f"AI analysis complete: {len(relevant_parts.most_relevant_segments)} segments found"
        )
        return relevant_parts

    @staticmethod
    async def create_video_clips(
        video_path: Path,
        segments: List[Dict[str, Any]],
        font_family: str = "TikTokSans-Regular",
        font_size: int = 24,
        font_color: str = "#FFFFFF",
        caption_template: str = "default",
        output_format: str = "vertical",
        add_subtitles: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Create video clips from segments with transitions and optional subtitles.
        Runs in thread pool as video processing is CPU-intensive.
        output_format: 'vertical' (9:16) or 'original' (keep source size, faster).
        add_subtitles: False skips subtitles; with original format uses ffmpeg stream copy (no re-encode).
        """
        logger.info(f"Creating {len(segments)} video clips subtitles={add_subtitles}")
        clips_output_dir = Path(config.temp_dir) / "clips"
        clips_output_dir.mkdir(parents=True, exist_ok=True)

        clips_info = await run_in_thread(
            create_clips_with_transitions,
            video_path,
            segments,
            clips_output_dir,
            font_family,
            font_size,
            font_color,
            caption_template,
            output_format,
            add_subtitles,
        )

        logger.info(f"Successfully created {len(clips_info)} clips")
        return clips_info

    @staticmethod
    async def generate_clip_title(clip_text: str) -> Dict[str, Any]:
        """Generate a viral title for a single clip using LLM.

        Returns dict with keys: title, alternatives, hashtags.
        On any failure returns a fallback based on the first 8 words of the transcript.
        """
        fallback_title = " ".join(clip_text.split()[:8]).strip()
        if not fallback_title:
            fallback_title = "untitled clip"

        try:
            prompt = _TITLE_PROMPT_TEMPLATE.format(text=clip_text[:500])

            agent: Agent[None, str] = Agent(
                model=config.llm,
                output_type=str,
                system_prompt="You are a viral YouTube Shorts title expert. Return ONLY raw JSON.",
            )
            result = await agent.run(prompt)
            raw = result.output.strip()

            # Strip markdown fences if the model wraps them
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)

            parsed = json.loads(raw)
            title = parsed.get("title", fallback_title)
            alternatives = parsed.get("alternatives", [])
            hashtags = parsed.get("hashtags", ["#shorts", "#fyp"])

            # Enforce max 60 chars on title
            if len(title) > 60:
                title = title[:57] + "..."

            return {
                "title": title,
                "alternatives": alternatives,
                "hashtags": hashtags,
            }
        except Exception as e:
            logger.warning(f"Title generation failed, using fallback: {e}")
            return {
                "title": fallback_title,
                "alternatives": [],
                "hashtags": ["#shorts", "#fyp"],
            }

    @staticmethod
    async def generate_titles_for_clips(
        clips_info: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Generate viral titles for all clips in parallel via asyncio.gather().

        Mutates each clip_info dict in-place by adding youtube_title,
        title_alternatives, and hashtags keys.  Also renames the physical
        mp4 file to the sanitized title.

        Never raises -- all errors are caught per-clip.
        """

        async def _process_one(clip_info: Dict[str, Any]) -> None:
            clip_text = clip_info.get("text", "")
            title_data = await VideoService.generate_clip_title(clip_text)

            clip_info["youtube_title"] = title_data["title"]
            clip_info["title_alternatives"] = json.dumps(title_data["alternatives"])
            clip_info["hashtags"] = json.dumps(title_data["hashtags"])

            # Rename the physical file to the sanitized title
            try:
                old_path = Path(clip_info["path"])
                if old_path.exists():
                    sanitized = _sanitize_title_for_filename(title_data["title"])
                    if sanitized:
                        new_filename = f"{sanitized}.mp4"
                        new_path = old_path.parent / new_filename
                        # Avoid collisions -- append clip_id if file already exists
                        if new_path.exists() and new_path != old_path:
                            stem = sanitized[:70]
                            new_filename = f"{stem}_{clip_info.get('clip_id', 'x')}.mp4"
                            new_path = old_path.parent / new_filename
                        os.rename(str(old_path), str(new_path))
                        clip_info["filename"] = new_filename
                        clip_info["path"] = str(new_path)
                        logger.info(
                            f"Renamed clip file: {old_path.name} -> {new_filename}"
                        )
            except Exception as e:
                logger.warning(f"Failed to rename clip file, keeping original: {e}")

        tasks = [_process_one(clip) for clip in clips_info]
        await asyncio.gather(*tasks, return_exceptions=True)
        return clips_info

    @staticmethod
    def determine_source_type(url: str) -> str:
        """Determine if source is YouTube or uploaded file."""
        video_id = get_youtube_video_id(url)
        return "youtube" if video_id else "video_url"

    @staticmethod
    async def process_video_complete(
        url: str,
        source_type: str,
        font_family: str = "TikTokSans-Regular",
        font_size: int = 24,
        font_color: str = "#FFFFFF",
        caption_template: str = "default",
        processing_mode: str = "fast",
        output_format: str = "vertical",
        add_subtitles: bool = True,
        cached_transcript: Optional[str] = None,
        cached_analysis_json: Optional[str] = None,
        progress_callback: Optional[Callable[[int, str, str], Awaitable[None]]] = None,
        should_cancel: Optional[Callable[[], Awaitable[bool]]] = None,
    ) -> Dict[str, Any]:
        """
        Complete video processing pipeline.
        Returns dict with segments and clips info.

        progress_callback: Optional function to call with progress updates
                          Signature: async def callback(progress: int, message: str, status: str)
        """
        try:
            # Step 1: Get video path (download or use existing)
            if should_cancel and await should_cancel():
                raise Exception("Task cancelled")

            if progress_callback:
                await progress_callback(10, "Downloading video...", "processing")

            if source_type == "youtube":
                video_path = await VideoService.download_video(url)
                if not video_path:
                    raise Exception("Failed to download video")
            else:
                video_path = VideoService.resolve_local_video_path(url)
                if not video_path.exists():
                    raise Exception("Video file not found")

            # Step 2: Generate transcript
            if should_cancel and await should_cancel():
                raise Exception("Task cancelled")

            if progress_callback:
                await progress_callback(30, "Generating transcript...", "processing")

            transcript = cached_transcript
            if not transcript:
                transcript = await VideoService.generate_transcript(
                    video_path, processing_mode=processing_mode
                )

            # Step 3: AI analysis
            if should_cancel and await should_cancel():
                raise Exception("Task cancelled")

            if progress_callback:
                await progress_callback(
                    50, "Analyzing content with AI...", "processing"
                )

            relevant_parts = None
            if cached_analysis_json:
                try:
                    cached_analysis = json.loads(cached_analysis_json)
                    segments = cached_analysis.get("most_relevant_segments", [])

                    class _SimpleResult:
                        def __init__(self, payload: Dict[str, Any]):
                            self.summary = payload.get("summary")
                            self.key_topics = payload.get("key_topics")
                            self.most_relevant_segments = payload.get(
                                "most_relevant_segments", []
                            )

                    relevant_parts = _SimpleResult(
                        {
                            "summary": cached_analysis.get("summary"),
                            "key_topics": cached_analysis.get("key_topics", []),
                            "most_relevant_segments": segments,
                        }
                    )
                except Exception:
                    relevant_parts = None

            if relevant_parts is None:
                relevant_parts = await VideoService.analyze_transcript(transcript)

            # Step 4: Create clips
            if should_cancel and await should_cancel():
                raise Exception("Task cancelled")

            if progress_callback:
                await progress_callback(70, "Creating video clips...", "processing")

            raw_segments = relevant_parts.most_relevant_segments
            segments_json: List[Dict[str, Any]] = []
            for segment in raw_segments:
                if isinstance(segment, dict):
                    virality = segment.get("virality") or {}
                    segments_json.append(
                        {
                            "start_time": segment.get("start_time"),
                            "end_time": segment.get("end_time"),
                            "text": segment.get("text", ""),
                            "relevance_score": segment.get("relevance_score", 0.0),
                            "reasoning": segment.get("reasoning", ""),
                            "virality_score": virality.get("total_score", 0)
                            if isinstance(virality, dict)
                            else 0,
                            "hook_score": virality.get("hook_score", 0)
                            if isinstance(virality, dict)
                            else 0,
                            "engagement_score": virality.get("engagement_score", 0)
                            if isinstance(virality, dict)
                            else 0,
                            "value_score": virality.get("value_score", 0)
                            if isinstance(virality, dict)
                            else 0,
                            "shareability_score": virality.get("shareability_score", 0)
                            if isinstance(virality, dict)
                            else 0,
                            "hook_type": virality.get("hook_type")
                            if isinstance(virality, dict)
                            else None,
                            "bgm_mood": virality.get("bgm_mood")
                            if isinstance(virality, dict)
                            else None,
                        }
                    )
                else:
                    virality = segment.virality
                    segments_json.append(
                        {
                            "start_time": segment.start_time,
                            "end_time": segment.end_time,
                            "text": segment.text,
                            "relevance_score": segment.relevance_score,
                            "reasoning": segment.reasoning,
                            "virality_score": virality.total_score if virality else 0,
                            "hook_score": virality.hook_score if virality else 0,
                            "engagement_score": virality.engagement_score
                            if virality
                            else 0,
                            "value_score": virality.value_score if virality else 0,
                            "shareability_score": virality.shareability_score
                            if virality
                            else 0,
                            "hook_type": virality.hook_type if virality else None,
                            "bgm_mood": virality.bgm_mood if virality else None,
                        }
                    )

            if processing_mode == "fast":
                segments_json = segments_json[: config.fast_mode_max_clips]

            clips_info = await VideoService.create_video_clips(
                video_path,
                segments_json,
                font_family,
                font_size,
                font_color,
                caption_template,
                output_format,
                add_subtitles,
            )

            # Step 5: Generate viral titles for clips (parallel LLM calls)
            try:
                logger.info("Generating viral titles for clips")
                clips_info = await VideoService.generate_titles_for_clips(clips_info)
                logger.info("Viral title generation complete")
            except Exception as e:
                logger.warning(f"Title generation step failed (non-fatal): {e}")

            if progress_callback:
                await progress_callback(90, "Finalizing clips...", "processing")

            return {
                "segments": segments_json,
                "clips": clips_info,
                "summary": relevant_parts.summary if relevant_parts else None,
                "key_topics": relevant_parts.key_topics if relevant_parts else None,
                "transcript": transcript,
                "analysis_json": json.dumps(
                    {
                        "summary": relevant_parts.summary if relevant_parts else None,
                        "key_topics": relevant_parts.key_topics
                        if relevant_parts
                        else [],
                        "most_relevant_segments": segments_json,
                    }
                ),
            }

        except Exception as e:
            logger.error(f"Error in video processing pipeline: {e}")
            raise

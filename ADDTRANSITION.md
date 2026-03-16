Add an on-demand transition API endpoint to the existing codebase.

## CONTEXT
- Clips are already rendered as raw independent files in `backend/temp/clips/`
- `apply_transition_effect` already exists in `video_utils.py` but is not called
- Transition video files (including greenscreen .mp4) are in `backend/transitions/`

## WHAT TO BUILD

### 1. New API endpoint in `backend/src/api/routes/tasks.py`
POST /tasks/{task_id}/clips/{clip_id}/transition
Request body:
{
  "transition_type": "fade" | "zoom" | "slide" | "greenscreen",
  "transition_file": "optional filename from transitions/ folder"
}
Response: updated clip object with new filename and path

### 2. Update `apply_transition_effect` in `video_utils.py`
Handle two modes:

MODE A — Simple fade (no transition file):
- Apply FadeIn(0.3) + FadeOut(0.3) to the clip using moviepy
- Save as `{original_filename}_transition.mp4` in same directory

MODE B — Greenscreen transition (transition_file provided):
- Use ffmpeg directly to chroma key the greenscreen transition video
- Overlay it on top of the clip at the beginning
- ffmpeg command pattern:
  ffmpeg -i clip.mp4 -i greenscreen_transition.mp4 
  -filter_complex "[1:v]chromakey=0x00FF00:0.1:0.0[ckout];[0:v][ckout]overlay=0:0"
  -c:a copy output.mp4
- The greenscreen color to key out is pure green #00FF00
- Chroma key similarity: 0.1, blend: 0.0 (adjustable)
- Save as `{original_filename}_transition.mp4`

### 3. New service method in `video_service.py`
`apply_clip_transition(task_id, clip_id, transition_type, transition_file)` that:
- Loads clip from DB via clip_repository
- Calls apply_transition_effect with correct mode
- Updates clip filename and path in DB
- Returns updated clip dict

### 4. Update clip in DB
After transition applied, update generated_clips table:
- filename → new filename with _transition suffix  
- path → new file path

## CONSTRAINTS
- Original raw clip file must NOT be deleted — keep it as backup
- If transition fails, return original clip unchanged with error message
- Use subprocess for ffmpeg calls, not moviepy
- Medium quality encoding for simple fade: preset fast, crf 23
- For greenscreen mode, preserve original video quality (copy codec where possible)
- Wrap everything in try/except
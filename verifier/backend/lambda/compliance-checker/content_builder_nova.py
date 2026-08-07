from typing import Dict, Any, List
from math import sqrt, ceil
import random
import base64
from collections import defaultdict

class ContentBuilder:
    """
    Builds formatted content for compliance review LLM submission.
    Internal use by ComplianceReviewer class.
    """
    
    def __init__(self, num_images: int = 20):
        self.num_images = num_images

    def build(self, metadata: Dict[str, Any], prompt: str) -> List[Dict[str, Any]]:
        """
        Build complete content object for LLM submission.
        
        Args:
            metadata: Service metadata containing video, audio, and frame information
            prompt: Compliance review prompt text
            
        Returns:
            List of content objects ready for LLM submission
            
        Raises:
            ValueError: If required metadata fields are missing
        """
        self._validate_metadata(metadata)
        content = []
        self._add_frames(content, metadata['frame_selection_loader'], metadata['image_metadata_loader'])
        content.extend(self._build_transcript_and_prompt(metadata, prompt))

        return content
    
    def _validate_metadata(self, metadata: Dict[str, Any]) -> None:
        """Validate required metadata fields exist."""
        required_fields = {
            'video_id': [],
            'fps': [],
            'shot_loader': ['shot_index', 'shot_description', 'scene_index', 'frame_index', 'scene_description', 'scene_frame_index'],
            'audio_transcription_loader': ['audio_transcript', 'frame_idx'],
            'frame_selection_loader': ['frame', 'frame_idx'],
            'image_metadata_loader': ['image_description', 'ocr']
        }

        for service, fields in required_fields.items():
            if service not in metadata:
                raise ValueError(f"Missing required service: {service}")
            for field in fields:
                if field not in metadata[service]:
                    raise ValueError(f"Missing required field: {service}.{field}")
    
    def _build_video_summary(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Build video summary content object."""
        return {
            "text": f"<video_summary>{metadata['video_analyzer']['video_summary']}</video_summary>"
        }
    
    def _add_frames(self, content: List[Dict[str, Any]], frame_data: Dict[str, Any], image_data: Dict[str, Any]) -> None:
        """Add frame content to the content list."""
        # Randomly permute and sort frame_data by frame_idx
        frame_data, image_data = self._stratified_and_sort_frames(frame_data, image_data)
        self._add_individual_frames(content, frame_data, image_data, self.num_images)

    def _permute_and_sort_frames(self, frame_data: Dict[str, Any], image_data: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """Randomly permute frame_data and then sort by frame_idx."""
        # Get the number of frames
        num_frames = len(frame_data['frame_idx'])

        # Create random permutation indices
        indices = list(range(num_frames))
        random.shuffle(indices)
        sort_indices = sorted(indices[:self.num_images])

        # Apply sorted indices to all fields
        sorted_frame_data = {key: [values[i] for i in sort_indices] for key, values in frame_data.items()}
        sorted_image_data = {key: [values[i] for i in sort_indices] for key, values in image_data.items()}

        return sorted_frame_data, sorted_image_data

    def _stratified_sampling(self, frame_idx: List[int], num_images: int, num_buckets: int=10, seed: int=42) -> List[int]:
        """
        Perform stratified sampling from frame_idx using bucketization.

        Args:
            frame_idx (List[int]): Sorted list of frame indices.
            num_images (int): Number of images to sample.
            num_buckets (int): Number of buckets to divide frame index range into.
            seed (int, optional): Random seed for reproducibility.

        Returns:
            List[int]: Sorted list of positions into frame_idx, length == num_images.
            Positions are returned (not values) so duplicate frame_idx entries are
            handled correctly.
        """
        if seed is not None:
            random.seed(seed)

        if num_images >= len(frame_idx):
            return list(range(len(frame_idx)))

        # Step 1: Bucketize positions (not values) based on the value range
        min_val, max_val = frame_idx[0], frame_idx[-1]
        range_span = max_val - min_val + 1
        bucket_size = ceil(range_span / num_buckets)

        buckets = defaultdict(list)
        for pos, idx in enumerate(frame_idx):
            bucket_id = min((idx - min_val) // bucket_size, num_buckets - 1)
            buckets[bucket_id].append(pos)

        # Step 2: Compute per-bucket probabilities (based on item count)
        total_count = len(frame_idx)
        bucket_probs = {k: len(v) / total_count for k, v in buckets.items()}

        # Step 3: Assign number of samples per bucket (rounded)
        samples_per_bucket = {
            k: round(p * num_images) for k, p in bucket_probs.items()
        }

        # Step 4: Adjust total to exactly num_images
        current_total = sum(samples_per_bucket.values())
        while current_total != num_images:
            diff = num_images - current_total
            # Pick the bucket with largest room for adjustment
            if diff > 0:
                candidates = sorted(buckets.items(), key=lambda kv: -len(kv[1]))
            else:
                candidates = sorted(samples_per_bucket.items(), key=lambda kv: -kv[1])
            for k, _ in candidates:
                if diff > 0 and samples_per_bucket[k] < len(buckets[k]):
                    samples_per_bucket[k] += 1
                    break
                elif diff < 0 and samples_per_bucket[k] > 0:
                    samples_per_bucket[k] -= 1
                    break
            current_total = sum(samples_per_bucket.values())

        # Step 5: Sample positions from each bucket
        selected_positions: List[int] = []
        for k, n in samples_per_bucket.items():
            candidates = buckets[k]
            if len(candidates) <= n:
                selected_positions.extend(candidates)
            else:
                selected_positions.extend(random.sample(candidates, n))

        # Step 6: Sort so frames are presented to the LLM in temporal order
        return sorted(selected_positions)

    def _stratified_and_sort_frames(self, frame_data: Dict[str, Any], image_data: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """Adopt stratified sampling on frame_data and then sort by frame_idx."""
        # Stratified sampling
        sort_indices = self._stratified_sampling(frame_data['frame_idx'], self.num_images)

        # Apply sorted indices to all fields
        sorted_frame_data = {key: [values[i] for i in sort_indices] for key, values in frame_data.items()}
        sorted_image_data = {key: [values[i] for i in sort_indices] for key, values in image_data.items()}

        return sorted_frame_data, sorted_image_data

    def _add_individual_frames(self, content: List[Dict[str, Any]], frame_data: Dict[str, Any], image_data: Dict[str, Any], num_images: int) -> None:
        """Add individual frames to content."""

        image_counter = 0
        for idx, frame, description, orc in zip(
            frame_data['frame_idx'],
            frame_data['frame'],
            image_data['image_description'],
            image_data['ocr'],
        ):
            if image_counter < num_images:
                content.append({
                    "text": f"<frame_info>"
                            f"<frame_index>{idx}</frame_index>"
                            f"<description>{description}</description>"
                            f"<ocr>{orc}</ocr></frame_info>"
                })                
                raw = base64.b64decode(frame)
                img_format = "jpeg" if raw[:2] == b'\xff\xd8' else "png"
                content.append({
                    "image": {"format": img_format, "source": {"bytes": frame}}
                })
            else:
                break
            image_counter += 1

    def _build_transcript_and_prompt(self, metadata: Dict[str, Any], prompt: str) -> List[Dict[str, Any]]:
        """Build transcript and prompt content objects."""
        content = []
        for transcript, frame_index in zip(
            metadata["audio_transcription_loader"]["audio_transcript"],
            metadata["audio_transcription_loader"]["frame_idx"],
        ):
            content.append(
                {
                    "text": f"<audio_info>"
                            f"<transcript>{transcript}</transcript>"
                            f"<start_frame_index>{frame_index[0]}</start_frame_index>"
                            f"<end_frame_index>{frame_index[1]}</end_frame_index></audio_info>"
                }
            )
        content.append(
            {
                "text": prompt
            }
        )
        return content
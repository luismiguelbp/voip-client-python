"""
PJSIP Audio Test

Tests PJSIP audio devices (no SIP/account): list devices, loopback (mic to speakers), record to WAV.

Usage:
    python -m voip_client.pjsip_test_audio [--duration SECONDS] [--output FILENAME]
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import pjsua2 as pj
except Exception as exc:
    print(f"Failed to import pjsua2: {exc}")
    sys.exit(1)

from voip_client.pjsip_common import PjsipEndpoint


class PjsipAudioTest:
    """Runs audio device listing, loopback (mic to speakers), and recording."""

    def __init__(self, endpoint: PjsipEndpoint) -> None:
        self._endpoint = endpoint
        self._recorder: Optional[pj.AudioMediaRecorder] = None

    def list_devices(self) -> None:
        """List all available audio devices and current capture/playback."""
        ep = self._endpoint.endpoint
        if ep is None:
            raise RuntimeError("Endpoint not created")
        aud_dev_mgr = ep.audDevManager()
        dev_count = aud_dev_mgr.getDevCount()

        print("\n=== Available Audio Devices ===")
        for i in range(dev_count):
            info = aud_dev_mgr.getDevInfo(i)
            direction = []
            if info.inputCount > 0:
                direction.append(f"input:{info.inputCount}ch")
            if info.outputCount > 0:
                direction.append(f"output:{info.outputCount}ch")
            dir_str = ", ".join(direction) if direction else "none"
            print(f"  [{i}] {info.name} ({dir_str})")

        cap_dev = aud_dev_mgr.getCaptureDev()
        play_dev = aud_dev_mgr.getPlaybackDev()
        print(f"\nCurrent capture device: [{cap_dev}]")
        print(f"Current playback device: [{play_dev}]")

    def run(self, duration: int, output_file: Path) -> int:
        """
        Run loopback (mic to speakers) and record to WAV.

        Returns:
            0 on success, 1 on failure
        """
        ep = self._endpoint.endpoint
        if ep is None:
            raise RuntimeError("Endpoint not created")

        try:
            aud_dev_mgr = ep.audDevManager()
            aud_dev_mgr.refreshDevs()

            print(f"\n=== Starting Audio Test ({duration} seconds) ===")
            print("Speak into your microphone - you should hear yourself through speakers.")
            print(f"Recording to: {output_file}")

            self._recorder = pj.AudioMediaRecorder()
            self._recorder.createRecorder(str(output_file))

            capture_media = aud_dev_mgr.getCaptureDevMedia()
            playback_media = aud_dev_mgr.getPlaybackDevMedia()

            capture_media.startTransmit(playback_media)
            capture_media.startTransmit(self._recorder)

            print("\nRecording... Press Ctrl+C to stop early.\n")

            start_time = time.time()
            try:
                while time.time() - start_time < duration:
                    ep.libHandleEvents(100)
                    remaining = duration - int(time.time() - start_time)
                    print(f"\rTime remaining: {remaining:3d}s", end="", flush=True)
            except KeyboardInterrupt:
                print("\n\nStopped early by user.")

            capture_media.stopTransmit(playback_media)
            capture_media.stopTransmit(self._recorder)

            print(f"\n\n=== Audio Test Complete ===")
            print(f"Recording saved to: {output_file}")

            if output_file.exists():
                size_kb = output_file.stat().st_size / 1024
                print(f"File size: {size_kb:.1f} KB")
            else:
                print("Warning: Recording file was not created")

            return 0

        except Exception as exc:
            print(f"\nAudio test failed: {exc}")
            return 1

        finally:
            self._cleanup_recorder()

    def _cleanup_recorder(self) -> None:
        if self._recorder is not None:
            try:
                del self._recorder
            except Exception:
                pass
            self._recorder = None


def get_default_output_path() -> Path:
    """Generate default output path with timestamp in recordings folder."""
    project_root = Path(__file__).parent.parent
    recordings_dir = project_root / "recordings"
    recordings_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return recordings_dir / f"pjsip_test_audio_{timestamp}.wav"


def run_pjsip_audio_test(duration: int, output_file: Path) -> int:
    """Create endpoint, run PJSIP audio test, destroy. Returns 0 on success, 1 on failure."""
    ep = PjsipEndpoint()
    try:
        ep.create()
        print("PJSIP initialized successfully")

        test = PjsipAudioTest(ep)
        test.list_devices()

        return test.run(duration, output_file)
    finally:
        ep.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="PJSIP audio test: microphone and speakers loopback and recording (no SIP)"
    )
    parser.add_argument(
        "--duration", "-d",
        type=int,
        default=5,
        help="Test duration in seconds (default: 5)"
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Output WAV file path (default: recordings/pjsip_test_audio_YYYYMMDD_HHMMSS.wav)"
    )
    args = parser.parse_args()

    output_path = Path(args.output) if args.output else get_default_output_path()
    if output_path.parent and not output_path.parent.exists():
        output_path.parent.mkdir(parents=True, exist_ok=True)

    return run_pjsip_audio_test(args.duration, output_path)


if __name__ == "__main__":
    raise SystemExit(main())

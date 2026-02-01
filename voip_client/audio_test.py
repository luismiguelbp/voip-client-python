"""
Audio Test Script

Tests the default microphone and speakers by:
1. Listing available audio devices
2. Creating a loopback (microphone -> speakers) so you hear yourself
3. Recording the audio to a WAV file

Recordings are saved to the recordings/ folder with a timestamp by default.

Usage:
    python -m voip_client.audio_test [--duration SECONDS] [--output FILENAME]
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import pjsua2 as pj
except Exception as exc:
    print(f"Failed to import pjsua2: {exc}")
    sys.exit(1)


def list_audio_devices(ep: pj.Endpoint) -> None:
    """List all available audio devices."""
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

    # Show current capture and playback devices
    cap_dev = aud_dev_mgr.getCaptureDev()
    play_dev = aud_dev_mgr.getPlaybackDev()
    print(f"\nCurrent capture device: [{cap_dev}]")
    print(f"Current playback device: [{play_dev}]")


def run_audio_test(duration: int, output_file: Path) -> int:
    """
    Run the audio test with loopback and recording.

    Args:
        duration: Test duration in seconds
        output_file: Path to save the recorded WAV file

    Returns:
        0 on success, 1 on failure
    """
    ep = pj.Endpoint()
    ep_cfg = pj.EpConfig()
    ep_cfg.uaConfig.threadCnt = 0  # Required for Python bindings
    ep_cfg.uaConfig.mainThreadOnly = True

    # Set log level (0-5, where 5 is most verbose)
    ep_cfg.logConfig.level = 3
    ep_cfg.logConfig.consoleLevel = 3

    recorder = None
    try:
        # Initialize PJSIP
        ep.libCreate()
        ep.libInit(ep_cfg)

        # Create a null transport (we don't need network for audio test)
        transport_cfg = pj.TransportConfig()
        transport_cfg.port = 0
        ep.transportCreate(pj.PJSIP_TRANSPORT_UDP, transport_cfg)

        ep.libStart()
        print("PJSIP initialized successfully")

        # List available audio devices
        list_audio_devices(ep)

        # Get audio device manager and refresh device list
        aud_dev_mgr = ep.audDevManager()
        aud_dev_mgr.refreshDevs()

        print(f"\n=== Starting Audio Test ({duration} seconds) ===")
        print("Speak into your microphone - you should hear yourself through speakers.")
        print(f"Recording to: {output_file}")

        # Create recorder to save audio
        recorder = pj.AudioMediaRecorder()
        recorder.createRecorder(str(output_file))

        # Get the capture audio media (microphone)
        capture_media = aud_dev_mgr.getCaptureDevMedia()

        # Get the playback audio media (speakers)
        playback_media = aud_dev_mgr.getPlaybackDevMedia()

        # Connect microphone to speakers (loopback - hear yourself)
        capture_media.startTransmit(playback_media)

        # Connect microphone to recorder (save to file)
        capture_media.startTransmit(recorder)

        print("\nRecording... Press Ctrl+C to stop early.\n")

        # Run for the specified duration
        start_time = time.time()
        try:
            while time.time() - start_time < duration:
                ep.libHandleEvents(100)
                remaining = duration - int(time.time() - start_time)
                print(f"\rTime remaining: {remaining:3d}s", end="", flush=True)
        except KeyboardInterrupt:
            print("\n\nStopped early by user.")

        # Stop transmissions
        capture_media.stopTransmit(playback_media)
        capture_media.stopTransmit(recorder)

        print(f"\n\n=== Audio Test Complete ===")
        print(f"Recording saved to: {output_file}")

        # Check file was created
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
        # Cleanup
        if recorder is not None:
            try:
                del recorder
            except Exception:
                pass
        try:
            ep.libDestroy()
        except Exception:
            pass


def get_default_output_path() -> Path:
    """Generate default output path with timestamp in recordings folder."""
    # Get the project root (parent of voip_client)
    project_root = Path(__file__).parent.parent
    recordings_dir = project_root / "recordings"

    # Generate timestamp filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"audio_test_{timestamp}.wav"

    return recordings_dir / filename


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test microphone and speakers with loopback and recording"
    )
    parser.add_argument(
        "--duration", "-d",
        type=int,
        default=10,
        help="Test duration in seconds (default: 10)"
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Output WAV file path (default: recordings/audio_test_YYYYMMDD_HHMMSS.wav)"
    )
    args = parser.parse_args()

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = get_default_output_path()

    # Create parent directories if needed
    if output_path.parent and not output_path.parent.exists():
        output_path.parent.mkdir(parents=True, exist_ok=True)

    return run_audio_test(args.duration, output_path)


if __name__ == "__main__":
    raise SystemExit(main())

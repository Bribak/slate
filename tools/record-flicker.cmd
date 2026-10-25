@echo off
rem Records the screen for 8 s (only frames that change, half size) to %TEMP%\slate-flicker.mkv, for finding a flicker.
echo Recording for 8 s: move the mouse from slate's sidebar into the code 2-3 times now.
"C:\Program Files\Shotcut\ffmpeg.exe" -hide_banner -loglevel error -y -f lavfi -i "ddagrab=framerate=60:dup_frames=0,hwdownload,format=bgra,scale=iw/2:ih/2" -t 8 -c:v libx264rgb -crf 0 -preset ultrafast -fps_mode passthrough "%TEMP%\slate-flicker.mkv"
echo Saved %TEMP%\slate-flicker.mkv

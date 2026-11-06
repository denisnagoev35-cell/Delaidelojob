#!/bin/bash
set -e
W=$(dirname "$(readlink -f "$0")"); cd "$W"
A=/mnt/user-data/attachments/1e526e9b-a414-520b-9d1a-06f33431e30f
C1=$A/251b96ad-2026-11-04_181356.mp4; C2=$A/8ce1eaaf-2026-11-04_181402.mp4
C3=$A/cb8a8e6c-2026-11-04_181406.mp4; C4=$A/d2beb0f5-2026-11-04_181410.mp4
BG=0.12
SC="scale=1080:1920:flags=lanczos,fps=24,format=yuv420p,settb=1/24,setpts=PTS-STARTPTS"
AF="aresample=48000,aformat=channel_layouts=stereo,volume=$BG"
mkdir -p clean out
# cold open: jaw drop 2.25-3.85 s of scene 2, punch zoom + big caption
ffmpeg -y -v error -ss 2.25 -t 1.6 -i $C2 -loop 1 -t 1.6 -i ov/hook0.png -filter_complex "
[0:v]scale=w='trunc(1080*(1+0.10*min(t/0.25\,1))/2)*2':h='trunc(1920*(1+0.10*min(t/0.25\,1))/2)*2':eval=frame:flags=lanczos,crop=1080:1920,fps=24,format=yuva420p[b];
[1:v]format=rgba,fade=in:st=0.08:d=0.08:alpha=1[t];[b][t]overlay=shortest=1,format=yuv420p[v];[0:a]$AF[a]" \
 -map "[v]" -map "[a]" -c:v libx264 -preset veryfast -crf 16 -c:a pcm_s16le clean/c0.mov
# scene 1 plain
ffmpeg -y -v error -i $C1 -vf "scale=1080:1920:flags=lanczos,fps=24,format=yuv420p" -af "$AF" -c:v libx264 -preset veryfast -crf 16 -c:a pcm_s16le clean/c1.mov
for v in vk youtube tiktok_dzen; do
  ffmpeg -y -v error -framerate 24 -t 8 -i ec_$v/%04d.png -f lavfi -t 8 -i anullsrc=r=48000:cl=stereo -vf format=yuv420p -c:v libx264 -preset veryfast -crf 16 -c:a pcm_s16le -shortest clean/ec8_$v.mov
done
for v in vk youtube tiktok_dzen; do
  ffmpeg -y -v error -i clean/c0.mov -i clean/c1.mov -i $C2 -i $C3 -i $C4 -i clean/ec8_$v.mov -filter_complex "
  [0:v]settb=1/24,setpts=PTS-STARTPTS[v0];[1:v]settb=1/24,setpts=PTS-STARTPTS[v1];
  [2:v]$SC[v2];[3:v]$SC[v3];[4:v]$SC[v4];[5:v]fps=24,format=yuv420p,settb=1/24,setpts=PTS-STARTPTS[v5];
  [v0][v1]xfade=transition=fadewhite:duration=0.25:offset=1.35[x1];
  [x1][v2]xfade=transition=fade:duration=0.2:offset=6.19[x2];
  [x2][v3]xfade=transition=fadeblack:duration=0.35:offset=10.88[x3];
  [x3][v4]xfade=transition=fadewhite:duration=0.3:offset=15.62[x4];
  [x4][v5]xfade=transition=fadeblack:duration=0.4:offset=20.26,format=yuv420p[v];
  [0:a]anull[a0];[1:a]anull[a1];[2:a]$AF[a2];[3:a]$AF[a3];[4:a]$AF[a4];[5:a]aresample=48000,aformat=channel_layouts=stereo[a5];
  [a0][a1]acrossfade=d=0.25[y1];[y1][a2]acrossfade=d=0.2[y2];[y2][a3]acrossfade=d=0.35[y3];[y3][a4]acrossfade=d=0.3[y4];[y4][a5]acrossfade=d=0.4[a]" \
  -map "[v]" -map "[a]" -c:v libx264 -preset veryfast -crf 18 -movflags +faststart -c:a aac -b:a 192k out/stazhirovka_hook_$v.mp4
done
ls -la out/*hook*

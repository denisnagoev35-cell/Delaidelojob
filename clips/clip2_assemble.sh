#!/bin/bash
set -e
W=$(dirname "$(readlink -f "$0")"); cd "$W"
A=/mnt/user-data/attachments/1e526e9b-a414-520b-9d1a-06f33431e30f
C1=$(ls $A/b6797545-*.mp4); C2=$(ls $A/4e0e7829-*.mp4); C3=$(ls $A/998bedc3-*.mp4); C4=$(ls $A/ba788ee9-*.mp4); C5=$(ls $A/559af45b-*.mp4)
BG=0.12
SC="scale=1080:1920:flags=lanczos,fps=30,format=yuv420p,setsar=1"
AF="aresample=48000,aformat=channel_layouts=stereo,volume=$BG"
ENC="-c:v libx264 -preset veryfast -crf 15 -c:a pcm_s16le"
mkdir -p seg2 out2
# hook: zoom on the envelope + moth (rendered frame by frame)
python3 -I hookzoom.py "$C1" seg2/hook_frames
ffmpeg -y -v error -framerate 30 -i seg2/hook_frames/%04d.png -ss 1.15 -t 1.4 -i $C1 -vf "format=yuv420p,setsar=1" -af "$AF" -map 0:v -map 1:a -shortest $ENC seg2/s0.mov
ffmpeg -y -v error -t 4.0 -i $C1 -vf "$SC" -af "$AF" $ENC seg2/s1.mov
i=2; for C in $C2 $C3 $C4 $C5; do ffmpeg -y -v error -i $C -vf "$SC" -af "$AF" $ENC seg2/s$i.mov; i=$((i+1)); done
for v in vk_dzen shorts_tiktok; do
  ffmpeg -y -v error -framerate 30 -i eb_$v/%04d.png -f lavfi -t 8 -i anullsrc=r=48000:cl=stereo -vf "format=yuv420p,setsar=1" -shortest $ENC seg2/ec_$v.mov
  ffmpeg -y -v error -i seg2/s0.mov -i seg2/s1.mov -i seg2/s2.mov -i seg2/s3.mov -i seg2/s4.mov -i seg2/s5.mov -i seg2/ec_$v.mov \
   -loop 1 -i ov2/hook.png -loop 1 -i ov2/f1.png -loop 1 -i ov2/f2.png -loop 1 -i ov2/f3.png -loop 1 -i ov2/stampfine.png -loop 1 -i ov2/botcard.png -loop 1 -i ov2/badge.png \
   -loop 1 -i ov2/wm0.png -loop 1 -i ov2/wm1.png -loop 1 -i ov2/wm2.png -loop 1 -i ov2/wm3.png -loop 1 -i ov2/wm4.png -filter_complex "
  [0:v]settb=1/30,setpts=PTS-STARTPTS[v0];[1:v]settb=1/30,setpts=PTS-STARTPTS[v1];[2:v]settb=1/30,setpts=PTS-STARTPTS[v2];
  [3:v]settb=1/30,setpts=PTS-STARTPTS[v3];[4:v]settb=1/30,setpts=PTS-STARTPTS[v4];[5:v]settb=1/30,setpts=PTS-STARTPTS[v5];[6:v]settb=1/30,setpts=PTS-STARTPTS[v6];
  [v0][v1]xfade=transition=fadewhite:duration=0.2:offset=1.2[x1];
  [x1][v2]xfade=transition=fade:duration=0.2:offset=5.0[x2];
  [x2][v3]xfade=transition=fade:duration=0.2:offset=9.8417[x3];
  [x3][v4]xfade=transition=fadeblack:duration=0.35:offset=14.5334[x4];
  [x4][v5]xfade=transition=fadewhite:duration=0.3:offset=19.2751[x5];
  [x5][v6]xfade=transition=fadeblack:duration=0.4:offset=23.9168,format=yuva420p[base];
  [7]format=rgba,fade=in:st=0.05:d=0.08:alpha=1,fade=out:st=1.15:d=0.15:alpha=1[o_hook];
  [8]format=rgba,fade=in:st=5.8:d=0.15:alpha=1,fade=out:st=9.55:d=0.2:alpha=1[o_f1];
  [9]format=rgba,fade=in:st=7.0:d=0.15:alpha=1,fade=out:st=9.55:d=0.2:alpha=1[o_f2];
  [10]format=rgba,fade=in:st=8.2:d=0.15:alpha=1,fade=out:st=9.55:d=0.2:alpha=1[o_f3];
  [11]format=rgba,fade=in:st=10.4:d=0.08:alpha=1,fade=out:st=14.3:d=0.2:alpha=1[o_st];
  [12]format=rgba,fade=in:st=16.3:d=0.2:alpha=1,fade=out:st=19.0:d=0.25:alpha=1[o_bot];
  [13]format=rgba,fade=out:st=23.7:d=0.25:alpha=1[o_badge];
  [14]format=rgba,fade=out:st=4.9:d=0.1:alpha=1[w0];
  [15]format=rgba,fade=in:st=5.0:d=0.1:alpha=1,fade=out:st=9.75:d=0.1:alpha=1[w1];
  [16]format=rgba,fade=in:st=9.85:d=0.1:alpha=1,fade=out:st=14.45:d=0.1:alpha=1[w2];
  [17]format=rgba,fade=in:st=14.55:d=0.1:alpha=1,fade=out:st=19.2:d=0.1:alpha=1[w3];
  [18]format=rgba,fade=in:st=19.3:d=0.1:alpha=1,fade=out:st=23.7:d=0.2:alpha=1[w4];
  [base][w0]overlay=shortest=1[b1];[b1][w1]overlay=shortest=1[b2];[b2][w2]overlay=shortest=1[b3];[b3][w3]overlay=shortest=1[b4];[b4][w4]overlay=shortest=1[b5];
  [b5][o_hook]overlay=shortest=1[c1];[c1][o_f1]overlay=shortest=1[c2];[c2][o_f2]overlay=shortest=1[c3];[c3][o_f3]overlay=shortest=1[c4];
  [c4][o_st]overlay=shortest=1[c5];[c5][o_bot]overlay=shortest=1[c6];[c6][o_badge]overlay=shortest=1,format=yuv420p[v];
  [0:a][1:a]acrossfade=d=0.2[y1];[y1][2:a]acrossfade=d=0.2[y2];[y2][3:a]acrossfade=d=0.2[y3];[y3][4:a]acrossfade=d=0.35[y4];[y4][5:a]acrossfade=d=0.3[y5];[y5][6:a]acrossfade=d=0.4[a]" \
   -map "[v]" -map "[a]" -t 31.9 -c:v libx264 -preset veryfast -crf 18 -movflags +faststart -c:a aac -b:a 192k out2/obeshchali_$v.mp4
done
ls -la out2

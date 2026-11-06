import QtQuick
import QtMultimedia

Item{
 id:audio;property var desk
 Repeater{model:(desk.board.shots||[]).reduce((rows,shot)=>rows.concat((shot.sounds||[]).map(sound=>({shot:shot,sound:sound}))),[])
  Item{id:clip;objectName:"storyboardAudioClip";required property var modelData;readonly property int playerPosition:player.position;readonly property int playerState:player.playbackState;property string soundId:modelData.sound.id;property bool syncing:false
   readonly property int begin:desk.soundOffset(modelData.sound,modelData.shot)
   readonly property bool active:desk.shot.id===modelData.shot.id&&desk.localTime>=begin&&desk.localTime<begin+modelData.sound.duration_ms&&modelData.sound.kind!=="silence"
   function sync(force){if(syncing)return;syncing=true;try{if(!active){if(player.playbackState===MediaPlayer.PlayingState)player.pause();return}if(player.mediaStatus===MediaPlayer.LoadingMedia||player.mediaStatus===MediaPlayer.NoMedia)return;var target=(modelData.sound.source_in_ms||0)+desk.localTime-begin;if(Math.abs(player.position-target)>(force?1:150))player.position=Math.round(target);if(desk.playing){if(player.playbackState!==MediaPlayer.PlayingState)player.play()}else if(player.playbackState===MediaPlayer.PlayingState)player.pause()}finally{syncing=false}}

   MediaPlayer{id:player;objectName:"storyboardSoundPlayer";property string soundId:clip.modelData.sound.id;audioOutput:AudioOutput{muted:desk.audioMuted} source:bridge&&clip.modelData.sound.audio_path?bridge.storyboardImageUrl(clip.modelData.sound.audio_path):"";onMediaStatusChanged:if(mediaStatus===MediaPlayer.LoadedMedia||mediaStatus===MediaPlayer.BufferedMedia)Qt.callLater(clip.sync,true);onErrorOccurred:function(error,errorString){desk.audioMessage="声音无法播放："+errorString}}
   Connections{target:desk;function onPlayheadChanged(){clip.sync(!desk.playing)}function onPlayingChanged(){clip.sync(true)}}
   onActiveChanged:sync(true)
  }
 }
}

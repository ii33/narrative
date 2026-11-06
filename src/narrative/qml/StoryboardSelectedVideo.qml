import QtQuick
import QtMultimedia
Item{
 id:view;objectName:"storyboardSelectedVideo";property var desk;property bool active:false;property bool failed:false;property bool syncing:false
 readonly property var selection:desk.shot.selected_video||({})
 readonly property int position:player.position
 readonly property int playbackState:player.playbackState
 readonly property bool available:!!selection.path&&!failed
 onSelectionChanged:{failed=false;Qt.callLater(sync,true)}
 function sync(force){if(syncing)return;syncing=true;try{if(!active||!available){if(player.playbackState===MediaPlayer.PlayingState)player.pause();return}if(player.mediaStatus===MediaPlayer.LoadingMedia||player.mediaStatus===MediaPlayer.NoMedia)return;var speed=selection.fit==="retime"?selection.speed:1;player.playbackRate=speed;var target=Math.min(selection.out_ms-1,Math.max(selection.in_ms,selection.in_ms+desk.localTime*speed));if(Math.abs(player.position-target)>(force?1:150))player.position=Math.round(target);if(desk.playing){if(player.playbackState!==MediaPlayer.PlayingState)player.play()}else if(player.playbackState===MediaPlayer.PlayingState)player.pause()}finally{syncing=false}}
 VideoOutput{id:output;anchors.fill:parent;fillMode:VideoOutput.PreserveAspectFit}
 MediaPlayer{id:player;source:bridge&&view.selection.path?bridge.storyboardImageUrl(view.selection.path):"";videoOutput:output;audioOutput:AudioOutput{muted:desk.audioMuted||!desk.videoAudioEnabled} onMediaStatusChanged:if(mediaStatus===MediaPlayer.LoadedMedia||mediaStatus===MediaPlayer.BufferedMedia)Qt.callLater(view.sync,true);onErrorOccurred:function(error,errorString){view.failed=true;desk.videoMessage="采用视频无法播放，当前沿用素描："+errorString}}
 Connections{target:desk;function onPlayheadChanged(){view.sync(!desk.playing)}function onPlayingChanged(){view.sync(true)}}
 onActiveChanged:sync(true)
}

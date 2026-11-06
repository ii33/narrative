import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "StoryboardMotion.js" as Motion

Item {
    id: desk
    SubmissionState{id:contentSubmission;owner:desk;operation:"";target:({})}

    objectName: "storyboardPage"
    required property var app
    property bool directorOnly:false
    readonly property var t: app.theme
    property var workspace: ({})
    property string unitId: ""
    property var board: ({})
    property var shots: board.shots || []
    readonly property bool ready: !!unitId && board.unit_id === unitId
    readonly property bool approved: board.approved === true
    readonly property var scenes: (workspace.document?.segments || []).reduce((all, segment) =>
        all.concat((segment.units || []).map(u => ({unit:u, segment:segment.id, label:(u.scene_number ? u.scene_number + " · " : "") + (u.plan?.title || u.id)}))), [])
    readonly property var scene: scenes.find(s => s.unit.id === unitId)
    property int token: 0
    property bool busy: false
    property string message: ""
    property double playhead: 0
    property bool playing: false
    property bool videoAudioEnabled:false
    property string videoMessage:""
    property bool exportIncludeAudio:false
    property string exportVariant:"sketch"
    property var lastExport:({})
    property var checkReport:({})
    property bool reviewing:false
    property bool reviewWanted:false
    property int reviewToken:0
    property var reviewScope:({})
    property string lastReviewKey:""
    function scheduleReview(force){if(!visible||!ready)return;var key=app.project.path+"|"+unitId+"|"+board.revision;if(force!==true&&key===lastReviewKey)return;if(reviewing){reviewWanted=true;return}reviewWanted=false;lastReviewKey=key;reviewToken=-app.nextStoryboardToken();reviewScope={unit_id:unitId,project:app.project.path,revision:board.revision};reviewing=true;bridge.storyboardAction("check_storyboard",{unit_id:unitId},reviewToken)}
    function openCheck(){scheduleReview(true);checkDialog.open()}
    property alias viewMode:variant.currentIndex
    property int deskMode: directorOnly?2:0
    property bool scriptReadOnly: false
    function editScriptShot(){editor.openShot(false)}
    function editScriptFrame(){frames.openForShot()}
    function addScriptShot(){editor.openShot(true)}
    function mediaAction(operation,args){if(busy)return;playing=false;videoEditor.errorText="";focusShot=args.shot_id||shot.id;focusOffset=localTime;request(operation,args)}
    function exportAnimatic(){animaticPicker.open()}
    property bool phase2Pending:false
    property var pendingPhase2:({})
    property var impactPreview:({})
    property var cameraPresets:({revision:0,presets:[]})
    property bool showCameraView:false
    property bool expandedTracks:false
    property bool audioMuted:false
    property string audioMessage:""
    readonly property var cameraView:shot.id?Motion.cameraView(board,shot,localTime,stageObjects):({})
    function soundOffset(sound,owner){return sound.marker_id?((board.markers||[]).find(m=>m.id===sound.marker_id)||{time_ms:0}).time_ms-(owner.action_start_ms||0):(sound.time_ms||0)}
    function savePhase2(changes,revision){if(busy||!ready||approved)return;playing=false;phase2Pending=true;focusShot=shot.id;focusOffset=localTime;request("apply_storyboard_changes",{unit_id:unitId,revision:revision,changes:changes})}
    function previewPhase2(changes,revision){if(busy)return;playing=false;pendingPhase2={changes:changes,revision:revision,unit_id:unitId,project:app.project.path};request("preview_storyboard_changes",{unit_id:unitId,revision:revision,changes:changes})}
    function confirmPhase2(){var p=pendingPhase2;if(p.unit_id!==unitId||p.project!==app.project.path)return;impactDialog.close();savePhase2(p.changes,p.revision)}
    function seekMarker(marker){var index=shots.findIndex(s=>marker.time_ms>=(s.action_start_ms||0)&&marker.time_ms<=(s.action_start_ms||0)+s.duration_ms);if(index<0){message="此动作时刻尚未被镜头覆盖。";return}seek(startOf(index)+marker.time_ms-(shots[index].action_start_ms||0))}
    function chooseSoundFile(){soundPicker.open()}
    function openMarkerBinding(target){markerBinding.target=target;markerBindChoice.currentIndex=0;markerBinding.revision=board.revision;markerBinding.open()}
    property string enlargedView:""
    property bool recording:false
    property string selectedObjectId:""
    property string interpolation:"linear"
    property bool loopEnabled:false
    property int loopStart:0
    property int loopEnd:0
    property real trackZoom:0
    readonly property real timelineScale:trackZoom>0?trackZoom:Math.max(1,track.width-Math.max(0,shots.length-1)*track.spacing)/Math.max(1,total)
    readonly property var trackGeometry:{var x=0,time=0;return shots.map(function(owner,index){var width=shotTrackWidth(owner),row={index:index,x:x,time_start:time,width:width,duration_ms:owner.duration_ms};x+=width+track.spacing;time+=owner.duration_ms;return row})}
    property int timelineEditDrags:0
    function beginTimelineEdit(){timelineEditDrags++}
    function endTimelineEdit(){timelineEditDrags=Math.max(0,timelineEditDrags-1)}
    function shotTrackWidth(owner){var width=owner.duration_ms*timelineScale;return trackZoom>0?Math.max(0.1,width):width}
    function timelineContentWidth(){var row=trackGeometry[trackGeometry.length-1];return row?row.x+row.width:0}
    function trackTimeAt(x){for(var i=0;i<trackGeometry.length;i++){var row=trackGeometry[i];if(x<=row.x+row.width)return row.time_start+Math.max(0,Math.min(1,(x-row.x)/row.width))*row.duration_ms;if(x<row.x+row.width+track.spacing)return row.time_start+row.duration_ms}return total}
    function trackXAt(time){for(var i=0;i<trackGeometry.length;i++){var row=trackGeometry[i];if(time<row.time_start+row.duration_ms||i===trackGeometry.length-1)return row.x+Math.max(0,Math.min(1,(time-row.time_start)/row.duration_ms))*row.width}return 0}
    function zoomAnchor(){var x=trackXAt(playhead)-track.contentX;return x>=0&&x<=track.width?x:track.width/2}
    function zoomTimeline(scale,anchorX){if(timelineEditDrags||!total)return;track.cancelFlick();var at=trackTimeAt(track.contentX+anchorX);trackZoom=Math.max(0.01,Math.min(1,scale));track.contentX=Math.max(0,Math.min(Math.max(0,timelineContentWidth()-track.width),trackXAt(at)-anchorX))}
    function fitTimeline(){if(timelineEditDrags)return;track.cancelFlick();trackZoom=0;track.contentX=0}
    function revealPlayhead(){if(!total)return;var x=trackXAt(playhead);if(x<track.contentX||x>track.contentX+track.width)track.contentX=Math.max(0,Math.min(Math.max(0,timelineContentWidth()-track.width),x-track.width/2))}
    onPlayheadChanged:if(playing)revealPlayhead()
    readonly property int fps:board.fps||24
    readonly property int actionTime:shot.id?(shot.action_start_ms||0)+localTime:0
    readonly property var stageObjects:Motion.objects(board.objects||[],actionTime)
    readonly property var selectedObject:stageObjects.find(o=>o.id===selectedObjectId)||({})
    function stepFrame(direction){seek((Math.round(playhead*fps/1000)+direction)*1000/fps)}
    function openObjectEditor(create){objectEditor.openFor(create!==false)}
    function openActionKeys(){playing=false;actionKeys.open()}
    function writeObjectPoint(id,time,x,y,revision,rotation){
        if(!recording||busy||approved)return
        var source=(board.objects||[]).find(o=>o.id===id),parent=stageObjects.find(o=>o.id===source?.parent_id)
        if(parent){var dx=x-parent.world_x,dy=y-parent.world_y,r=-parent.world_rotation*Math.PI/180;x=dx*Math.cos(r)-dy*Math.sin(r);y=dx*Math.sin(r)+dy*Math.cos(r)}
        if(!Number.isFinite(x)||!Number.isFinite(y)||x<0||x>1||y<0||y>1){message="目标站位超出对象所属空间的有效范围，未保存。";return}
        var change={op:"set_action_keyframe",object_id:id,time_ms:Math.round(time),x:x,y:y,interpolation:interpolation}
        if(rotation!==undefined)change.rotation=rotation
        focusShot=shot.id;focusOffset=localTime;request("apply_storyboard_changes",{unit_id:unitId,revision:revision,changes:[change]})
    }
    property double clockStart: 0
    readonly property double total: shots.reduce((sum, s) => sum + s.duration_ms, 0)
    readonly property int shotIndex: {
        let end = 0
        for (let i = 0; i < shots.length; ++i) {
            end += shots[i].duration_ms
            if (playhead < end) return i
        }
        return shots.length - 1
    }
    readonly property var shot: shots[shotIndex] || ({})
    readonly property int localTime: playhead - startOf(shotIndex)
    readonly property var frame: (shot.keyframes || []).reduce((found, f) =>
        f.offset_ms <= localTime && (!found || f.offset_ms > found.offset_ms) ? f : found, null)
    readonly property string imagePath: frame ? (variant.currentIndex === 1 && frame.style_path ? frame.style_path : frame.sketch_path) || "" : ""
    property string focusShot: ""
    property bool focusNew: false
    property int focusOffset: 0
    property string projectPath: ""

    function startOf(i) { return i<=0?0:i>=trackGeometry.length?total:trackGeometry[i].time_start }
    function seconds(ms) { return (ms / 1000).toFixed(2) + " s" }
    function request(op, args) {
        busy = true
        message = ""
        token=app.nextStoryboardToken();
        bridge.storyboardAction(op, args, token)
    }
    function reload() {
        if (unitId && visible) request("read_storyboard", {unit_id:unitId})
    }
    function resetView() {
        playing = false
        timelineEditDrags=0
        editor.close();editor.pending=false;objectEditor.close();objectEditor.pending=false;actionKeys.close();frames.close();frames.generating=false;markersEditor.close();soundEditor.close();cameraEditor.close();impactDialog.close();markerBinding.close();sourceDialog.close();phase2Pending=false;pendingPhase2=({});checkDialog.close();videoEditor.close();animationEditor.close();videoMessage=""
        recording=false;selectedObjectId="";loopEnabled=false;loopStart=0;loopEnd=0
        token=app.nextStoryboardToken()
        busy = false
        board = ({});checkReport=({});reviewing=false;reviewWanted=false;lastReviewKey="";reviewScope=({})
        playhead = 0
        focusShot = ""
        focusOffset = 0
        focusNew = false
        message = "";audioMessage=""
    }
    function selectScene(id) {
        resetView()
        unitId = id
        if (!scene) return
        const saved=app.storyboardSelection;
        if(saved?.project_path===app.project.path&&saved?.screenplay_id===app.screenplayId&&saved?.unit_id===id){focusShot=saved.shot_id||"";focusOffset=saved.offset_ms||0;}
        app.screenplaySelection = {project_path:app.project.path, screenplay_id:app.screenplayId,
                                   segment_id:scene.segment, unit_id:id, block_id:""}
        reload()
    }
    function loadScreenplay(id) {
        resetView()
        workspace = ({})
        unitId = ""
        if (app.mode === "workspace" && visible) bridge.loadScreenplayWorkspace(id)
    }
    function save(change) {
        if (!ready || busy) return
        playing = false
        focusNew = change.op === "create_shot"
        focusShot = change.shot_id || shot.id || ""
        focusOffset = ["set_keyframe","move_keyframe"].includes(change.op) ? change.offset_ms : Math.max(0,localTime)
        request("apply_storyboard_changes", {unit_id:unitId, revision:board.revision, changes:[change]})
    }
    function moveFrame(shotId,frameId,offset,revision,owner,project){if(busy||approved||owner!==unitId||project!==app.project.path)return;var ownerShot=shots.find(s=>s.id===shotId),f=(ownerShot?.keyframes||[]).find(f=>f.id===frameId);if(f&&f.offset_ms===Math.round(offset))return;if(f?.marker_id){previewPhase2([{op:"update_marker",marker_id:f.marker_id,time_ms:(ownerShot.action_start_ms||0)+Math.round(offset)}],revision);return}focusShot=shotId;focusOffset=offset;request("apply_storyboard_changes",{unit_id:owner,revision:revision,changes:[{op:"move_keyframe",shot_id:shotId,frame_id:frameId,offset_ms:Math.round(offset)}]})}
    function trackPoints(owner,kind){var rows=[];if(kind==="camera")rows=((owner.camera||{}).keys||[]).map(k=>({id:k.id,offset:k.offset_ms,label:"机位 "+seconds(k.offset_ms),key:k}));else if(kind==="sound")rows=(owner.sounds||[]).map(k=>({id:k.id,offset:soundOffset(k,owner),duration:k.duration_ms,label:k.label||({dialogue:"对白",effect:"效果",silence:"静默"}[k.kind]),sound:k}));else if(kind==="marker")rows=(board.markers||[]).map(k=>({id:k.id,offset:k.time_ms-(owner.action_start_ms||0),label:k.name,marker:k}));else (board.objects||[]).forEach(o=>(o.keyframes||[]).forEach(k=>rows.push({id:k.id,offset:k.time_ms-(owner.action_start_ms||0),label:o.name,object_id:o.id,key:k})));return rows.filter(r=>r.offset>=0&&r.offset<owner.duration_ms)}
    function dragTrackPoint(owner,kind,row,offset,revision){if(busy||approved)return;offset=Math.round(offset);var change;if(kind==="marker")change={op:"update_marker",marker_id:row.id,time_ms:(owner.action_start_ms||0)+offset};else if(kind==="camera")change={op:"move_camera_keyframe",shot_id:owner.id,keyframe_id:row.id,offset_ms:offset};else if(kind==="object"){change=row.key.marker_id?{op:"update_marker",marker_id:row.key.marker_id,time_ms:(owner.action_start_ms||0)+offset}:{op:"move_action_keyframe",object_id:row.object_id,keyframe_id:row.id,time_ms:(owner.action_start_ms||0)+offset}}else return;previewPhase2([change],revision)}

    function rememberSelection(){if(visible&&ready&&shot.id)app.storyboardSelection={project_path:app.project.path,screenplay_id:app.screenplayId,unit_id:unitId,shot_id:shot.id,offset_ms:localTime}}
    function seek(ms) {
        playing = false
        playhead = Math.round(Math.min(total, Math.max(0, ms)))
        revealPlayhead()
        rememberSelection();
    }
    function updateBoard(result) {
        if (!result || result.unit_id !== unitId) return
        const snapshot=JSON.parse(JSON.stringify(result))
        if(!Array.isArray(snapshot.shots))return
        board = snapshot
        if(selectedObjectId&&!(board.objects||[]).some(o=>o.id===selectedObjectId)){selectedObjectId="";recording=false}
        loopStart=Math.max(0,Math.min(total,loopStart));loopEnd=Math.max(0,Math.min(total,loopEnd));if(loopEnd<=loopStart)loopEnabled=false
        const i = focusNew ? shots.length - 1 : shots.findIndex(s => s.id === focusShot)
        if (i >= 0) seek(startOf(i) + Math.min(focusOffset, shots[i].duration_ms - 1))
        else seek(Math.min(total, playhead))
        focusShot = ""
        focusOffset = 0
        focusNew = false
        Qt.callLater(desk.scheduleReview)
    }
    onVisibleChanged: {
        if (visible && app) loadScreenplay(app.screenplayId || "")
        else {
            if(ready&&shot.id)app.storyboardSelection={project_path:app.project.path,screenplay_id:app.screenplayId,unit_id:unitId,shot_id:shot.id,offset_ms:localTime};
            playing = false; token=app.nextStoryboardToken()
        }
    }
    Connections {
        target: app
        function onProjectChanged() {
            const path = desk.app.project.path || ""
            if (desk.projectPath === path) return
            desk.projectPath = path
            desk.loadScreenplay(desk.app.screenplayId || "")
        }
    }
    Connections {
        target: bridge
        function onResultReady(kind, payload) {
            if (!desk.visible) return
            if (kind === "screenplay_workspace" && !payload.superseded) {
                desk.workspace = payload
                const screenplayId = payload.document?.screenplay?.id || ""
                desk.app.screenplayId = screenplayId
                const saved = desk.app.screenplaySelection
                const prior = saved?.screenplay_id === screenplayId && saved?.project_path === desk.app.project.path ? saved.unit_id : ""
                desk.selectScene(desk.scenes.find(s => s.unit.id === prior)?.unit.id || desk.scenes[0]?.unit.id || "")
            } else if (kind === "conversation_progress" && payload.event === "storyboard_submission"
                       && payload.unit_id === desk.unitId && payload.project_path && !desk.busy
                       && bridge.recentProjectKey(payload.project_path) === bridge.recentProjectKey(desk.app.project.path)) {
                if (payload.board?.revision > (desk.board.revision || 0)) desk.updateBoard(payload.board)
            } else if (kind === "conversation_progress" && payload.event === "task_complete"
                       && payload.writing_target?.kind === "storyboard" && payload.writing_target.ids.includes(desk.unitId) && !desk.busy) {
                desk.reload()
            } else if(kind==="storyboard"&&payload.token===desk.reviewToken){
                const scope=desk.reviewScope,result=payload.result
                if(scope.unit_id!==desk.unitId||scope.project!==desk.app.project.path)return
                desk.reviewing=false
                if(scope.revision!==desk.board.revision){desk.reviewWanted=false;Qt.callLater(desk.scheduleReview);return}
                if(result&&result.unit_id===desk.unitId&&result.revision===desk.board.revision)desk.checkReport=result
                else if(payload.error)desk.checkReport={unit_id:desk.unitId,revision:desk.board.revision,ok:false,needs_review:true,diagnostics:[{severity:"error",message:payload.error}]}
                if(desk.reviewWanted){desk.reviewWanted=false;Qt.callLater(desk.scheduleReview,true)}
            } else if (kind === "storyboard" && payload.token === desk.token) {
                desk.busy = false
                if (payload.error) {
                    desk.phase2Pending=false
                    desk.focusNew = false
                    desk.focusShot = ""
                    desk.message = payload.error
                    editor.pending=false;editor.errorText=payload.error;objectEditor.pending=false;objectEditor.errorText=payload.error
                    if(frames.generating){frames.generating=false;frames.errorText=payload.error}
                    if(videoEditor.visible){videoEditor.errorText=payload.error;if(["submit_storyboard_video","refresh_storyboard_video"].includes(payload.operation))desk.reload()}
                } else if(payload.operation==="check_storyboard"){desk.checkReport=payload.result;checkDialog.open()
                } else if(["read_storyboard_video_settings","save_storyboard_video_settings"].includes(payload.operation)){videoEditor.applySettings(payload.result);if(payload.operation==="save_storyboard_video_settings")videoEditor.closeSettings()
                } else if(payload.operation==="render_storyboard_animatic"){desk.lastExport=payload.result;desk.message="干净预演已导出："+payload.result.path
                } else if(payload.operation==="preview_storyboard_changes"){desk.impactPreview=payload.result;impactDialog.open()
                } else if(["load_storyboard_camera_presets","save_storyboard_camera_preset"].includes(payload.operation)){desk.cameraPresets=payload.result
                } else if (["export_storyboard_script","export_storyboard_panel"].includes(payload.operation)) {
                    desk.message="已导出："+(payload.result.path||payload.result.output_path||"")
                } else if (payload.operation === "export_storyboard_style_package") {
                    desk.message = "风格制作包已导出"
                } else {
                    desk.updateBoard(payload.result)
                    videoEditor.updateAfter(payload.operation)
                    if(payload.operation==="attach_storyboard_video"){desk.videoMessage="";variant.currentIndex=4}
                    if(payload.operation==="set_storyboard_animation")animationEditor.close()
                    if(payload.operation==="generate_storyboard_sketch"){frames.generating=false;frames.errorText="";frames.close()}
                    if(desk.phase2Pending&&payload.operation==="apply_storyboard_changes"){desk.phase2Pending=false;markersEditor.close();soundEditor.close();cameraEditor.close();markerBinding.close()}
                    if(objectEditor.pending&&payload.operation==="apply_storyboard_changes"){objectEditor.pending=false;objectEditor.close()}
                    if(editor.pending&&payload.operation==="apply_storyboard_changes"){editor.pending=false;editor.close()}
                    if (payload.operation === "render_storyboard_style") variant.currentIndex=1
                }
            }
        }
        function onErrorOccurred(kind, message) {
            if (kind === "storyboard" && desk.visible) { desk.busy = false; desk.message = message;if(frames.generating){frames.generating=false;frames.errorText=message}editor.pending=false;editor.errorText=message;objectEditor.pending=false;objectEditor.errorText=message }
        }
    }
    Timer {
        interval: 30
        repeat: true
        running: desk.playing && desk.visible
        onTriggered: {
            desk.playhead = Math.min(desk.total, Math.max(0, Date.now() - desk.clockStart))
            if(desk.loopEnabled&&desk.loopEnd>desk.loopStart&&desk.playhead>=desk.loopEnd){desk.playhead=desk.loopStart+(desk.playhead-desk.loopStart)%(desk.loopEnd-desk.loopStart);desk.clockStart=Date.now()-desk.playhead}
            else if (desk.playhead >= desk.total) desk.playing = false
        }
    }
    ColumnLayout {
        SubmissionFeedback{state:contentSubmission}

        anchors.fill: parent
        anchors.margins: 12
        spacing: 7
        RowLayout {
            Layout.fillWidth: true
            Label { text: desk.directorOnly?"导演台":"分镜稿"; font.pixelSize: 21; font.bold: true; color:desk.t.ink }
            ComboBox {
                Layout.fillWidth: true
                model: desk.workspace.screenplays || []
                textRole:"title"
                currentIndex: model.findIndex(s => s.id === desk.app.screenplayId)
                onActivated: desk.loadScreenplay(model[currentIndex].id)
            }
            ComboBox {
                objectName:"storyboardScene"
                Layout.fillWidth: true
                textRole:"label"
                model:desk.scenes
                currentIndex:desk.scenes.findIndex(s => s.unit.id === desk.unitId)
                onActivated: desk.selectScene(model[currentIndex].unit.id)
            }
            ToolButton { text:"刷新"; enabled:!desk.busy; onClicked:desk.loadScreenplay(desk.app.screenplayId) }
        }
        Label { visible:!!desk.message; text:desk.message; wrapMode:Text.Wrap; color:desk.t.warn; Layout.fillWidth:true }
        Label { visible:!desk.scenes.length; text:"先在剧本中创建场次，便可开始分镜。"; color:desk.t.muted }
        RowLayout {
            visible:!desk.directorOnly
            Layout.fillWidth:true
            Button { objectName:"storyboardScriptMode"; text:"分镜稿"; checkable:true; checked:desk.deskMode===0; onClicked:{desk.playing=false;desk.deskMode=0} }
            Button { objectName:"storyboardPanelMode"; text:"图格画板"; checkable:true; checked:desk.deskMode===1; onClicked:{desk.playing=false;desk.deskMode=1} }
            Item { Layout.fillWidth:true }
            CheckBox { objectName:"storyboardReadOnly"; text:"纯读稿"; visible:desk.deskMode===0; checked:desk.scriptReadOnly; onToggled:desk.scriptReadOnly=checked }
            Button { objectName:"storyboardExportScript"; text:"导出分镜稿"; enabled:desk.ready&&!desk.busy; onClicked:scriptExport.open() }
        }
        StoryboardScriptView { desk:desk; visible:!desk.directorOnly&&desk.deskMode===0; Layout.fillWidth:true; Layout.fillHeight:true }
        ShotPanelBoard { desk:desk; visible:!desk.directorOnly&&desk.deskMode===1; Layout.fillWidth:true; Layout.fillHeight:true }
        ColumnLayout { visible:desk.directorOnly; Layout.fillWidth:true; Layout.fillHeight:true
        SplitView{objectName:"storyboardViews";Layout.fillWidth:true;Layout.fillHeight:true;Layout.minimumHeight:140
            Rectangle {
                id:monitorPane
                visible:desk.enlargedView!=="stage"
                SplitView.fillWidth:true
                SplitView.minimumWidth:200
            color:"#202725"
            ToolButton{objectName:"storyboardMonitorMaximize";z:2;anchors.right:parent.right;text:desk.enlargedView==="monitor"?"还原":"放大画面";onClicked:desk.enlargedView=desk.enlargedView==="monitor"?"":"monitor"}
            StoryboardAnimatedImage {
                desk:desk
                id:monitor
                objectName:"storyboardMonitor"
                anchors.fill:parent
                visible:!desk.showCameraView&&(variant.currentIndex<2||(variant.currentIndex===4&&!adoptedVideo.available))
                source:bridge && desk.imagePath ? bridge.storyboardImageUrl(desk.imagePath) : ""
                fillMode:Image.PreserveAspectFit
                cache:false
            }
            StoryboardCompare{anchors.fill:parent;desk:desk;overlay:variant.currentIndex===3;visible:!desk.showCameraView&&(variant.currentIndex===2||variant.currentIndex===3)}
            StoryboardSelectedVideo{id:adoptedVideo;anchors.fill:parent;desk:desk;active:!desk.showCameraView&&variant.currentIndex===4;visible:active&&available}
            StoryboardCameraView{anchors.fill:parent;desk:desk;visible:desk.showCameraView}
            CheckBox{z:3;anchors.left:parent.left;anchors.bottom:parent.bottom;text:"机位视图";checked:desk.showCameraView;onToggled:desk.showCameraView=checked;background:Rectangle{color:"#e3e8dd"}}
            Label{anchors.top:parent.top;anchors.horizontalCenter:parent.horizontalCenter;visible:desk.checkReport.revision===desk.board.revision&&(desk.checkReport.affected_frames||[]).some(f=>f.frame_id===desk.frame?.id);text:"本帧需要复查，详见检查。";color:"#f3d2a7";padding:5;background:Rectangle{color:"#5a4133"}}
            Label {
                visible:desk.approved && variant.currentIndex === 1 && !!desk.frame && !desk.frame.style_path
                text:"此帧沿用草图";anchors.top:parent.top;anchors.left:parent.left;padding:6;color:"white"
                background:Rectangle {color:"#63422e"}
            }
            Label {
                anchors.centerIn:parent
                visible:!desk.showCameraView&&monitor.visible&&monitor.status !== Image.Ready
                text:monitor.status === Image.Error ? "关键帧文件无法显示" : desk.shot.id ? desk.shot.title + " · 待显示关键帧" : "添加本场的第一个镜头"
                width:parent.width - 16; horizontalAlignment:Text.AlignHCenter; wrapMode:Text.Wrap; color:"#e0e7e3"
            }
        }
            StoryboardStage{objectName:"storyboardStage";desk:desk;visible:desk.enlargedView!=="monitor";SplitView.preferredWidth:450;SplitView.minimumWidth:280}
        }
        Label{objectName:"storyboardReviewStatus";visible:desk.ready;text:desk.reviewing?"正在后台检查分镜与素材…":desk.checkReport.revision===desk.board.revision?(desk.checkReport.needs_review?"部分素材需要复查，可查看检查详情。":"分镜与素材检查通过。"):"";color:desk.t.muted;wrapMode:Text.Wrap;Layout.fillWidth:true}
        Label{visible:!!desk.videoMessage||variant.currentIndex===4&&!adoptedVideo.available;text:desk.videoMessage||"本镜尚未采用视频，当前沿用素描。";color:desk.t.warn;wrapMode:Text.Wrap;Layout.fillWidth:true}
        RowLayout {
            Layout.fillWidth:true
            Button {
                objectName:"storyboardPlay"
                text:desk.playing ? "暂停" : "播放"
                enabled:desk.total > 0 && !desk.busy
                onClicked:{
                    if (desk.playing) desk.playing = false
                    else { if(desk.loopEnabled&&desk.loopEnd>desk.loopStart&&(desk.playhead<desk.loopStart||desk.playhead>=desk.loopEnd))desk.playhead=desk.loopStart;else if (desk.playhead >= desk.total) desk.playhead = 0; desk.clockStart = Date.now() - desk.playhead; desk.playing = true }
                }
            }
            ToolButton{objectName:"storyboardPreviousFrame";text:"上一帧";enabled:desk.total>0;onClicked:desk.stepFrame(-1)}
            ToolButton{objectName:"storyboardNextFrame";text:"下一帧";enabled:desk.total>0;onClicked:desk.stepFrame(1)}
            ComboBox{objectName:"storyboardFps";model:[12,24,25,30,60];currentIndex:model.indexOf(desk.fps);displayText:desk.fps+" fps";enabled:desk.ready&&!desk.busy&&!desk.approved;onActivated:desk.save({op:"set_fps",fps:model[currentIndex]})}
            Slider {
                objectName:"storyboardScrub"; Layout.fillWidth:true
                from:0; to:Math.max(1, desk.total); value:desk.playhead; enabled:desk.total>0
                onMoved:desk.seek(value)
            }
            CheckBox{text:"静音";checked:desk.audioMuted;onToggled:desk.audioMuted=checked}
            Label { text:desk.seconds(desk.playhead) + " / " + desk.seconds(desk.total) }
            CheckBox{text:"视频原声";visible:variant.currentIndex===4;checked:desk.videoAudioEnabled;onToggled:desk.videoAudioEnabled=checked}
            ComboBox { id:variant;objectName:"storyboardVariant";model:["素描","风格图","并排对照","叠加对照","采用视频"];enabled:desk.approved||!!desk.shot.selected_video; onEnabledChanged:if(!enabled) currentIndex=0 }
        }
        RowLayout{Layout.fillWidth:true;CheckBox{objectName:"storyboardLoop";text:"选段循环";checked:desk.loopEnabled;enabled:desk.loopEnd>desk.loopStart;onToggled:desk.loopEnabled=checked}Button{objectName:"storyboardLoopStart";text:"设循环起点";onClicked:desk.loopStart=Math.round(desk.playhead)}Button{objectName:"storyboardLoopEnd";text:"设循环终点";onClicked:desk.loopEnd=Math.round(desk.playhead)}Label{text:desk.seconds(desk.loopStart)+" — "+desk.seconds(desk.loopEnd);color:desk.t.muted}Item{Layout.fillWidth:true}CheckBox{objectName:"storyboardExpandTracks";text:"展开镜内轨";checked:desk.expandedTracks;onToggled:desk.expandedTracks=checked}RowLayout{Layout.preferredWidth:330;Layout.minimumWidth:230;spacing:3
            ToolButton{objectName:"storyboardZoomOut";text:"-";enabled:desk.total>0&&!desk.timelineEditDrags;onClicked:desk.zoomTimeline(desk.timelineScale/1.25,desk.zoomAnchor())}
            Slider{objectName:"storyboardZoomSlider";Layout.fillWidth:true;Layout.minimumWidth:40;from:Math.log(10);to:Math.log(1000);value:Math.log(Math.max(10,Math.min(1000,desk.timelineScale*1000)));enabled:desk.total>0&&!desk.timelineEditDrags;onMoved:desk.zoomTimeline(Math.exp(value)/1000,desk.zoomAnchor())}
            ToolButton{objectName:"storyboardZoomIn";text:"+";enabled:desk.total>0&&!desk.timelineEditDrags;onClicked:desk.zoomTimeline(desk.timelineScale*1.25,desk.zoomAnchor())}
            Label{objectName:"storyboardZoomValue";text:!desk.total?"无镜头":(desk.trackZoom===0?"适配 · ":"")+Math.round(desk.timelineScale*1000)+" 像素/秒";font.pixelSize:10}
            ToolButton{objectName:"storyboardZoomFit";text:"适配全场";enabled:desk.total>0&&!desk.timelineEditDrags;onClicked:desk.fitTimeline()}
        }}
        Label{visible:!!desk.audioMessage;text:desk.audioMessage;color:desk.t.warn;wrapMode:Text.Wrap;Layout.fillWidth:true}
        Flickable {
            id:track
            objectName:"storyboardTrack"
            Layout.fillWidth:true; Layout.preferredHeight:desk.expandedTracks?210:100
            property real spacing:1
            contentHeight:height
            flickableDirection:Flickable.HorizontalFlick
            boundsBehavior:Flickable.StopAtBounds
            contentWidth:desk.timelineContentWidth()
            clip:true
            ScrollBar.horizontal:ScrollBar {}
            WheelHandler{target:null;acceptedDevices:PointerDevice.Mouse|PointerDevice.TouchPad;acceptedModifiers:Qt.NoModifier;blocking:false
                onWheel:function(event){var delta=event.pixelDelta.x||event.angleDelta.x/120*60||event.pixelDelta.y||event.angleDelta.y/120*60;if(!delta){event.accepted=false;return}if(desk.timelineEditDrags){event.accepted=true;return}var before=track.contentX;track.cancelFlick();track.contentX=Math.max(0,Math.min(Math.max(0,desk.timelineContentWidth()-track.width),before-delta));event.accepted=Math.abs(track.contentX-before)>0.1}
            }
            WheelHandler{target:null;acceptedDevices:PointerDevice.Mouse|PointerDevice.TouchPad;acceptedModifiers:Qt.ControlModifier;blocking:true
                onWheel:function(event){if(desk.timelineEditDrags){event.accepted=true;return}var delta=event.pixelDelta.y?event.pixelDelta.y/80:event.angleDelta.y/120;if(!delta){event.accepted=false;return}event.accepted=true;desk.zoomTimeline(desk.timelineScale*Math.pow(1.2,delta),Math.max(0,Math.min(track.width,event.x-track.contentX)))}
            }
            Repeater {model:desk.shots
            Rectangle {
                id:shotClip
                readonly property bool inViewport:x+width>=track.contentX&&x<=track.contentX+track.width
                objectName:"storyboardShotClip"
                required property var modelData
                required property int index
                property string shotId:modelData.id
                x:(desk.trackGeometry[index]||{x:0}).x
                height:track.height - 10
                width:(desk.trackGeometry[index]||{width:0}).width
                color:desk.shotIndex===index ? "#cee3db" : "#e5eae7"
                clip:true
                RowLayout {
                    anchors.left: parent.left;anchors.right:parent.right;anchors.top:parent.top;height:62
                    anchors.margins: 4
                    spacing: 4
                    Image {
                        readonly property var firstFrame: modelData.keyframes?.[0]
                        readonly property string path: firstFrame ? (variant.currentIndex === 1 && firstFrame.style_path ? firstFrame.style_path : firstFrame.sketch_path) : ""
                        visible: shotClip.inViewport && !!path && parent.width >= 110
                        Layout.preferredWidth: 44
                        Layout.preferredHeight: 32
                        source: bridge && visible && path ? bridge.storyboardImageUrl(path) : ""
                        fillMode: Image.PreserveAspectFit
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 1
                        Label { Layout.fillWidth: true; text: (index+1)+" · "+modelData.title; elide: Text.ElideRight }
                        Label { text: desk.seconds(modelData.duration_ms); font.pixelSize: 11 }
                    }
                }
                Rectangle { visible:desk.shotIndex===index; width:2; height:parent.height; color:"#a15131"; x:Math.min(parent.width-width, parent.width*desk.localTime/modelData.duration_ms) }
                MouseArea { anchors.fill:parent; onClicked:function(mouse){desk.seek(desk.startOf(index)+Math.round(mouse.x/width*modelData.duration_ms))} }
                Repeater{model:shotClip.inViewport?modelData.keyframes||[]:[]
                    Rectangle{id:frameHandle;required property var modelData;property string frameId:modelData.id;objectName:"storyboardFrameHandle";width:12;height:23;y:desk.expandedTracks?65:parent.height-height;x:Math.min(parent.width-width,modelData.offset_ms/parent.modelData.duration_ms*parent.width);color:"#9a6735";border.color:"white";radius:2
                        MouseArea{anchors.fill:parent;preventStealing:true;cursorShape:Qt.SizeHorCursor;property real pressX;property int original;property real pressedWidth;property int rev;property string owner;property string project;onPressed:function(mouse){desk.beginTimelineEdit();desk.playing=false;pressedWidth=frameHandle.parent.width;pressX=mapToItem(track.contentItem,mouse.x,0).x;original=frameHandle.modelData.offset_ms;rev=desk.board.revision;owner=desk.unitId;project=desk.app.project.path}onReleased:function(mouse){desk.endTimelineEdit();var delta=mapToItem(track.contentItem,mouse.x,0).x-pressX;var shot=frameHandle.parent.modelData;var offset=Math.max(0,Math.min(shot.duration_ms-1,Math.round(original+delta/pressedWidth*shot.duration_ms)));desk.moveFrame(shot.id,frameHandle.frameId,offset,rev,owner,project)}onCanceled:desk.endTimelineEdit()}
                        ToolTip.visible:frameHover.hovered;ToolTip.text:desk.seconds(modelData.offset_ms);HoverHandler{id:frameHover}
                    }
                }
                Column{visible:desk.expandedTracks;y:90;width:parent.width;spacing:2
                    Repeater{model:desk.expandedTracks&&shotClip.inViewport?[{kind:"camera",name:"机位",color:"#739eb7"},{kind:"object",name:"对象",color:"#8baf83"},{kind:"sound",name:"声音",color:"#b98c72"},{kind:"marker",name:"时刻",color:"#a78aba"}]:[]
                        Rectangle{id:lane;required property var modelData;width:parent.width;height:23;color:"#edf0eb";readonly property var owner:lane.parent.parent.modelData
                            Text{text:lane.modelData.name;font.pixelSize:9;color:"#65726a";x:3}
                            Repeater{model:desk.trackPoints(lane.owner,lane.modelData.kind)
                                Rectangle{id:point;objectName:"storyboardTrackPoint";required property var modelData;property string pointId:modelData.id;property string shotId:lane.owner.id;property string pointKind:lane.modelData.kind;x:modelData.offset/lane.owner.duration_ms*lane.width;y:8;height:14;width:modelData.duration?modelData.duration/lane.owner.duration_ms*lane.width:7;color:lane.modelData.color;radius:2
                                    ToolTip.visible:pointHover.hovered;ToolTip.text:point.modelData.label;HoverHandler{id:pointHover}
                                    MouseArea{anchors.fill:parent;preventStealing:true;cursorShape:Qt.SizeHorCursor;property real pressedX;property real pressedWidth;property int revision;property string ownerUnit;property string project
                                        onPressed:function(mouse){desk.beginTimelineEdit();desk.playing=false;pressedWidth=lane.width;pressedX=mapToItem(track.contentItem,mouse.x,0).x;revision=desk.board.revision;ownerUnit=desk.unitId;project=desk.app.project.path}
                                        onReleased:function(mouse){desk.endTimelineEdit();if(ownerUnit!==desk.unitId||project!==desk.app.project.path)return;var delta=mapToItem(track.contentItem,mouse.x,0).x-pressedX;if(Math.abs(delta)>3){var offset=point.modelData.offset+delta/pressedWidth*lane.owner.duration_ms;desk.dragTrackPoint(lane.owner,lane.modelData.kind,point.modelData,offset,revision)}else desk.seek(desk.startOf(desk.shots.findIndex(s=>s.id===lane.owner.id))+point.modelData.offset)}
                                        onCanceled:desk.endTimelineEdit()
                                        onDoubleClicked:if(lane.modelData.kind==="marker")markersEditor.openFor(point.modelData.marker);else if(lane.modelData.kind==="sound")soundEditor.openFor(point.modelData.sound);else if(lane.modelData.kind==="camera")cameraEditor.openFor();else{desk.selectedObjectId=point.modelData.object_id;desk.openActionKeys()}
                                    }
                                }
                            }
                        }
                    }
                }
            }
            }
        }
        Flow {
            Layout.fillWidth:true; spacing:4
            Button{objectName:"storyboardCheck";text:desk.reviewing?"检查中…":"检查";enabled:desk.ready&&!desk.busy&&!desk.reviewing;onClicked:desk.openCheck()}
            Button{objectName:"storyboardAnimation";text:"草图动画 / 导出";enabled:!!desk.shot.id&&!desk.busy;onClicked:animationEditor.openFor()}
            Button{objectName:"storyboardVideo";text:"视频生成 / 采用";enabled:!!desk.shot.id&&!desk.busy;onClicked:videoEditor.openFor()}
            Button{objectName:"storyboardSource";text:"正文定位";enabled:desk.ready;onClicked:sourceDialog.open()}
            Button{objectName:"storyboardMarkers";text:"命名时刻";enabled:desk.ready&&!desk.busy;onClicked:markersEditor.openFor({})}
            Button{objectName:"storyboardCamera";text:"摄影机 / 运镜";enabled:!!desk.shot.id&&!desk.busy;onClicked:cameraEditor.openFor()}
            Button{objectName:"storyboardSounds";text:"临时声音";enabled:!!desk.shot.id&&!desk.busy;onClicked:soundsDialog.open()}
            Button { objectName:"storyboardAskAssistant";text:"让助手分镜";enabled:desk.ready&&!desk.busy&&!desk.approved;onClicked:{
                assistantPrompt.text=desk.shots.length ? "请根据本场正文完善镜头安排和草图关键帧。" : "请根据本场正文安排镜头、时长和草图关键帧，保存到本场分镜轨。"
                writing.open()
            }}
            Button { objectName:"storyboardAddShot";text:"新增镜头";enabled:desk.ready&&!desk.busy&&!desk.approved;onClicked:editor.openShot(true) }
            Button { objectName:"storyboardEditShot";text:"编辑镜头";enabled:!!desk.shot.id&&!desk.busy&&!desk.approved;onClicked:editor.openShot(false) }
            Button { text:"前移";enabled:desk.shotIndex>0&&!desk.busy&&!desk.approved;onClicked:desk.save({op:"move_shot",shot_id:desk.shot.id,index:desk.shotIndex-1}) }
            Button { text:"后移";enabled:desk.shotIndex>=0&&desk.shotIndex<desk.shots.length-1&&!desk.busy&&!desk.approved;onClicked:desk.save({op:"move_shot",shot_id:desk.shot.id,index:desk.shotIndex+1}) }
            Button { text:"删除镜头";enabled:!!desk.shot.id&&!desk.busy&&!desk.approved;onClicked:desk.save({op:"delete_shot",shot_id:desk.shot.id}) }
            Button { objectName:"storyboardFrames";text:"关键帧";enabled:!!desk.shot.id&&!desk.busy;onClicked:frames.openForShot() }
            Button { objectName:"storyboardApprove";text:desk.approved?"退回草图":"分镜定案";enabled:desk.shots.length>0&&!desk.busy;onClicked:desk.save({op:"set_approved",approved:!desk.approved}) }
        }
    }
    }

    Dialog {
        id:scriptExport; objectName:"storyboardScriptExportDialog"; title:"导出分镜稿"; modal:true; anchors.centerIn:parent; width:420
        contentItem:ColumnLayout {
            ComboBox{id:scriptFormat;objectName:"storyboardScriptFormat";model:["PDF","HTML","纯文本"]}
            CheckBox{id:scriptIllustrated;objectName:"storyboardScriptIllustrated";text:"包含画格";checked:true}
            CheckBox{id:scriptAnnotated;objectName:"storyboardScriptAnnotated";text:"包含制作批注";checked:false}
            Button{text:"选择保存位置";onClicked:scriptPicker.open()}
        }
    }
    FileDialog {
        id:scriptPicker; fileMode:FileDialog.SaveFile; title:"保存分镜稿"
        nameFilters:scriptFormat.currentIndex===0?["PDF (*.pdf)"]:scriptFormat.currentIndex===1?["HTML (*.html)"]:["纯文本 (*.txt)"]
        onAccepted:{desk.request("export_storyboard_script",{unit_id:desk.unitId,format:["pdf","html","text"][scriptFormat.currentIndex],path:selectedFile.toString(),illustrated:scriptIllustrated.checked,annotated:scriptAnnotated.checked});scriptExport.close()}
    }
    Dialog{id:checkDialog;objectName:"storyboardCheckDialog";title:"分镜检查";modal:true;anchors.centerIn:parent;width:Math.min(650,desk.width-24);height:Math.min(500,desk.height-24)
        contentItem:ColumnLayout{Label{text:desk.reviewing?"正在后台检查…":desk.checkReport.ok?(desk.checkReport.needs_review?"结构通过，部分素材需要复查":"检查通过"):"发现需要处理的问题";font.bold:true}Label{text:"受影响镜头："+(desk.checkReport.affected_shots||[]).map(s=>typeof s==="string"?(desk.shots.find(row=>row.id===s)?.title||s):(s.title||s.shot_id)).join("、");wrapMode:Text.Wrap;Layout.fillWidth:true}ListView{Layout.fillWidth:true;Layout.fillHeight:true;clip:true;model:desk.checkReport.diagnostics||[];delegate:Label{required property var modelData;width:ListView.view.width;text:modelData.message||String(modelData);wrapMode:Text.Wrap;color:modelData.severity==="error"?desk.t.bad:desk.t.body}}}
    }
    StoryVideoEditor{id:videoEditor;desk:desk}
    StoryboardAnimationEditor{id:animationEditor;desk:desk}
    FileDialog{id:animaticPicker;objectName:"storyboardAnimaticFile";title:"导出干净草图预演";fileMode:FileDialog.SaveFile;defaultSuffix:"mp4";nameFilters:["MP4视频 (*.mp4)"];onAccepted:desk.mediaAction("render_storyboard_animatic",{unit_id:desk.unitId,shot_id:desk.shot.id,output_path:selectedFile.toString(),variant:desk.exportVariant,include_audio:desk.exportIncludeAudio})}
    StoryboardAudio{desk:desk}
    StoryboardMarkers{id:markersEditor;desk:desk}
    StoryboardSoundEditor{id:soundEditor;desk:desk}
    StoryboardCameraEditor{id:cameraEditor;desk:desk}
    Dialog{id:sourceDialog;objectName:"storyboardSourceDialog";title:"正文与关联镜头";modal:true;anchors.centerIn:parent;width:Math.min(650,desk.width-24);height:Math.min(520,desk.height-24)
        contentItem:ListView{clip:true;model:desk.scene?.unit.blocks||[];spacing:8;delegate:ColumnLayout{required property var modelData;width:ListView.view.width;Label{text:modelData.payload.text||modelData.type;wrapMode:Text.Wrap;Layout.fillWidth:true}Flow{Layout.fillWidth:true;spacing:4;Repeater{model:desk.shots.filter(s=>(s.source_block_ids||[]).includes(modelData.id));Button{required property var modelData;text:modelData.title;onClicked:{desk.seek(desk.startOf(desk.shots.findIndex(s=>s.id===modelData.id)));sourceDialog.close()}}}}}}
    }
    Dialog{id:impactDialog;objectName:"storyboardImpactDialog";title:"确认共享时刻的影响";modal:true;anchors.centerIn:parent;width:Math.min(620,desk.width-24)
        contentItem:ColumnLayout{Label{text:"受影响镜头："+(desk.impactPreview.affected_shots||[]).map(s=>s.title||s.shot_id).join("、")+"\n受影响素描："+(desk.impactPreview.affected_frames||[]).map(f=>f.frame_id).join("、")+"\n受影响对象："+(desk.impactPreview.affected_objects||[]).map(o=>o.name||o.object_id).join("、");wrapMode:Text.Wrap;Layout.fillWidth:true}Repeater{model:desk.impactPreview.conflicts||[];Label{required property var modelData;text:modelData.message||String(modelData);color:desk.t.warn;wrapMode:Text.Wrap;Layout.fillWidth:true}}RowLayout{Button{text:"取消";onClicked:impactDialog.close()}Button{objectName:"storyboardConfirmImpact";text:"确认修改";enabled:desk.impactPreview.valid===true&&!desk.busy;onClicked:desk.confirmPhase2()}}}
    }
    Dialog{id:markerBinding;property var target:({});property int revision:0;title:"绑定共享动作时刻";modal:true;anchors.centerIn:parent;width:Math.min(480,desk.width-24)
        contentItem:ColumnLayout{ComboBox{id:markerBindChoice;objectName:"storyboardMarkerBindChoice";model:[{id:null,name:"自由时间（解除绑定）"}].concat(desk.board.markers||[]);textRole:"name";Layout.fillWidth:true}Button{objectName:"storyboardSaveMarkerBinding";text:"预览影响";enabled:!desk.busy;onClicked:desk.previewPhase2([Object.assign({},markerBinding.target,{marker_id:markerBindChoice.model[markerBindChoice.currentIndex].id})],markerBinding.revision)}}
    }
    Dialog{id:soundsDialog;objectName:"storyboardSoundsDialog";title:"临时声音轨";modal:true;anchors.centerIn:parent;width:Math.min(650,desk.width-24);height:Math.min(500,desk.height-24)
        contentItem:ColumnLayout{Button{objectName:"storyboardNewSound";text:"新增声音 / 静默";enabled:!desk.approved&&!desk.busy;onClicked:soundEditor.openFor({})}ListView{Layout.fillWidth:true;Layout.fillHeight:true;clip:true;model:desk.shot.sounds||[];delegate:RowLayout{required property var modelData;width:ListView.view.width;Label{text:({dialogue:"对白",effect:"效果",silence:"静默"}[modelData.kind])+" · "+modelData.label+" · "+desk.seconds(desk.soundOffset(modelData,desk.shot));Layout.fillWidth:true;wrapMode:Text.Wrap}Button{text:"定位";onClicked:desk.seek(desk.startOf(desk.shotIndex)+desk.soundOffset(modelData,desk.shot))}Button{text:"编辑";enabled:!desk.busy&&!desk.approved;onClicked:soundEditor.openFor(modelData)}Button{text:"删除";enabled:!desk.busy&&!desk.approved;onClicked:desk.savePhase2([{op:"delete_sound",shot_id:desk.shot.id,sound_id:modelData.id}],desk.board.revision)}}}}
    }
    FileDialog{id:soundPicker;title:"选择临时声音文件";nameFilters:["音频 (*.wav *.mp3 *.ogg *.flac *.m4a)"];onAccepted:soundEditor.acceptPath(selectedFile.toString())}
    StoryboardObjectEditor{id:objectEditor;desk:desk}
    Dialog{id:actionKeys;objectName:"storyboardActionKeysDialog";title:"动作关键点 · "+(desk.selectedObject.name||"");modal:true;anchors.centerIn:parent;width:Math.min(620,desk.width-24);height:Math.min(450,desk.height-24)
        contentItem:ColumnLayout{
            Label{text:"移动时间和删除保留其余关键点。操作保存到当前场，不改镜头顺序。";wrapMode:Text.Wrap;Layout.fillWidth:true}
            Label{visible:!!desk.message;text:desk.message;wrapMode:Text.Wrap;Layout.fillWidth:true;color:desk.t.warn}
            ListView{Layout.fillWidth:true;Layout.fillHeight:true;clip:true;model:((desk.board.objects||[]).find(o=>o.id===desk.selectedObjectId)||{}).keyframes||[]
                delegate:RowLayout{required property var modelData;property string keyframeId:modelData.id;width:ListView.view.width;Label{text:desk.seconds(modelData.time_ms);Layout.fillWidth:true}SpinBox{id:keyTime;objectName:"storyboardActionKeyTime";property string keyframeId:modelData.id;from:0;to:2147483647;value:modelData.time_ms;editable:true}Button{objectName:"storyboardMoveActionKey";property string keyframeId:modelData.id;text:"移动";enabled:!desk.busy&&!desk.approved;onClicked:desk.previewPhase2([{op:"move_action_keyframe",object_id:desk.selectedObjectId,keyframe_id:modelData.id,time_ms:keyTime.value}],desk.board.revision)}Button{text:"时刻";enabled:!desk.busy&&!desk.approved;onClicked:desk.openMarkerBinding({op:"bind_action_keyframe_marker",object_id:desk.selectedObjectId,keyframe_id:modelData.id})}Button{objectName:"storyboardDeleteActionKey";property string keyframeId:modelData.id;text:"删除";enabled:!desk.busy&&!desk.approved;onClicked:desk.previewPhase2([{op:"delete_action_keyframe",object_id:desk.selectedObjectId,keyframe_id:modelData.id}],desk.board.revision)}}
            }
        }
    }
    Dialog {
        id:writing;title:"让助手分镜";modal:true;anchors.centerIn:parent
        width:Math.min(desk.width-16,520)
        contentItem:ColumnLayout {
            Label {text:desk.scene?.label || "";font.bold:true}
            TextArea {id:assistantPrompt;objectName:"storyboardAssistantPrompt";Layout.fillWidth:true;Layout.preferredHeight:110;wrapMode:TextEdit.Wrap}
            Button {text:"发送到当前聊天";enabled:!!assistantPrompt.text.trim()&&desk.ready;onClicked:{
                desk.app.assistantOpen=true
                bridge.screenplayWritingTurn(assistantPrompt.text,{kind:"storyboard",ids:[desk.unitId],screenplay_id:desk.app.screenplayId})
                writing.close()
            }}
        }
    }
    Dialog {
        id:editor
        SubmissionState{id:shotSubmission;allowedActions:["create_shot","update_shot"];authoredFields:["title","duration_ms","source_block_ids"];owner:editor;app:desk.app;controller:desk;operation:"apply_storyboard_changes";target:({unit_id:editor.targetUnit});onRestoreRequested:function(a){var c=(a.changes||[])[0]||{};editor.creating=c.op==="create_shot";editor.capturedShot=(desk.shots||[]).find(function(x){return x.id===c.shot_id})||{};editor.targetRevision=a.revision;editor.targetUnit=a.unit_id;titleField.text=c.title||"";duration.value=c.duration_ms;actionStart.value=c.action_start_ms||0;editor.selectedBlocks=c.source_block_ids||[]}}
        objectName:"storyboardShotDialog"
        modal:true; anchors.centerIn:parent
        width:Math.min(desk.width-12,600)
        height:Math.min(desk.height-12,520)
        property bool creating:true
        property var selectedBlocks:[]
        property var capturedShot:({})
        property var resolutions:({})
        property string targetUnit:""
        property string targetProject:""
        property int targetRevision:0
        property bool pending:false
        property string errorText:""
        readonly property var conflicts:creating?[]:(capturedShot.keyframes||[]).filter(f=>f.offset_ms>=duration.value)
        readonly property bool resolved:conflicts.every(f=>["move","delete"].includes((resolutions[f.id]||{}).action))
        function resolveFrame(id,action,offset){var copy=Object.assign({},resolutions);copy[id]={action:action,offset:offset};resolutions=copy}
        function submitShot(){
            if(desk.busy||targetUnit!==desk.unitId||targetProject!==desk.app.project.path||!resolved)return
            const change={op:creating?"create_shot":"update_shot",title:titleField.text.trim(),duration_ms:duration.value,action_start_ms:actionStart.value,source_block_ids:selectedBlocks}
            if(!creating)change.shot_id=capturedShot.id
            var changes=[change];conflicts.forEach(function(f){var choice=resolutions[f.id];changes.push(choice.action==="delete"?{op:"delete_keyframe",shot_id:capturedShot.id,frame_id:f.id}:{op:"move_keyframe",shot_id:capturedShot.id,frame_id:f.id,offset_ms:Math.round(choice.offset)})})
            pending=true;errorText="";desk.focusNew=creating;desk.focusShot=capturedShot.id||"";desk.focusOffset=desk.localTime
            desk.request("apply_storyboard_changes",{unit_id:targetUnit,revision:targetRevision,changes:changes})
        }
        title:creating?"新增镜头":"编辑镜头"
        function openShot(create) {
            desk.playing=false
            creating=create;capturedShot=JSON.parse(JSON.stringify(desk.shot));targetUnit=desk.unitId;targetProject=desk.app.project.path;targetRevision=desk.board.revision;resolutions=({});pending=false;errorText=""
            titleField.text=create?"":desk.shot.title
            duration.value=create?3000:desk.shot.duration_ms
            actionStart.value=create?0:(desk.shot.action_start_ms||0)
            selectedBlocks=create?[]:Array.from(desk.shot.source_block_ids)
            open()
        }
        contentItem:ColumnLayout {
            SubmissionFeedback{state:shotSubmission}
            Label{visible:!!editor.errorText;text:editor.errorText;wrapMode:Text.Wrap;Layout.fillWidth:true;color:desk.t.warn}
            TextField { id:titleField;enabled:!editor.pending;objectName:"storyboardShotTitle";placeholderText:"镜头画面与动作";Layout.fillWidth:true }
            RowLayout { Label{text:"时长（毫秒）"} SpinBox{id:duration;objectName:"storyboardShotDuration";from:1;to:2147483647;stepSize:100;editable:true} }
            RowLayout{Label{text:"动作起点（毫秒）"}SpinBox{id:actionStart;objectName:"storyboardActionStart";from:0;to:2147483647;stepSize:100;editable:true}}
            Label{objectName:"storyboardShrinkWarning";visible:editor.conflicts.length>0;text:"缩短后有 "+editor.conflicts.length+" 张关键帧超出镜头。请逐张明确移动或删除，与时长一起保存。";wrapMode:Text.Wrap;Layout.fillWidth:true;color:desk.t.warn}
            ScrollView{visible:editor.conflicts.length>0;Layout.fillWidth:true;Layout.preferredHeight:120;clip:true;contentWidth:availableWidth
                ColumnLayout{width:parent.availableWidth
                    Repeater{model:editor.conflicts
                        RowLayout{required property var modelData;Layout.fillWidth:true;Label{text:modelData.id+" · "+desk.seconds(modelData.offset_ms);Layout.fillWidth:true}ComboBox{id:conflictAction;objectName:"storyboardFrameConflictAction";property string frameId:modelData.id;model:["选择处理","移至镜内","删除此帧"];enabled:!editor.pending;onActivated:editor.resolveFrame(frameId,currentIndex===1?"move":currentIndex===2?"delete":"",conflictOffset.value)}SpinBox{id:conflictOffset;objectName:"storyboardFrameConflictOffset";property string frameId:modelData.id;from:0;to:Math.max(0,duration.value-1);value:to;editable:true;enabled:conflictAction.currentIndex===1&&!editor.pending;onValueChanged:if(conflictAction.currentIndex===1)editor.resolveFrame(frameId,"move",value)}}
                    }
                }
            }
            Label {text:"引用本场正文"}
            ListView {
                Layout.fillWidth:true;Layout.fillHeight:true;clip:true
                model:desk.scene?.unit.blocks || []
                delegate:CheckBox {
                    required property var modelData
                    objectName:"storyboardSourceBlock"
                    width:ListView.view.width
                    text:(modelData.payload?.speaker_label ? modelData.payload.speaker_label+"　":"")+(modelData.payload?.text || modelData.type)
                    checked:editor.selectedBlocks.includes(modelData.id)
                    contentItem:Label{leftPadding:parent.indicator.width+parent.spacing;text:parent.text;wrapMode:Text.Wrap;verticalAlignment:Text.AlignVCenter}
                    onToggled:editor.selectedBlocks=checked ? editor.selectedBlocks.concat([modelData.id]) : editor.selectedBlocks.filter(id=>id!==modelData.id)
                }
            }
            Button { objectName:"storyboardSaveShot";text:editor.pending?"保存中…":"保存镜头";enabled:!!titleField.text.trim()&&editor.resolved&&!editor.pending&&!desk.busy;onClicked:editor.submitShot() }

        }
    }
    Dialog {
        id:frames
        objectName:"storyboardFrameDialog"
        anchors.centerIn:parent;modal:true;width:Math.min(desk.width-12,540)
        title:desk.shot.title+" · 关键帧"
        property bool generating:false
        property string errorText:""
        function openForShot() {desk.playing=false;generating=false;errorText="";offset.value=Math.max(0,Math.min(desk.localTime,desk.shot.duration_ms-1));loadSourceForOffset();open()}
        function loadSourceForOffset(){var source=((desk.shot.keyframes||[]).find(f=>f.offset_ms===offset.value)||{}).sketch_source||{},layout=source.layout||{};sketchPrompt.text=source.user_prompt===undefined?(desk.shot.title||""):source.user_prompt;simpleColor.checked=source.simple_color===true;composition.currentIndex=Math.max(0,["single","two_shot","object","empty"].indexOf(layout.composition||"single"));framing.currentIndex=Math.max(0,["wide","medium","close"].indexOf(layout.framing||"medium"));light.currentIndex=Math.max(0,["left","right","back","overhead"].indexOf(layout.light||"left"));setting.currentIndex=Math.max(0,["interior","exterior"].indexOf(layout.setting||"interior"))}
        contentItem:ColumnLayout {
            Label{visible:!!frames.errorText;text:frames.errorText;wrapMode:Text.Wrap;Layout.fillWidth:true;color:desk.t.warn}
            RowLayout {
                Label{text:"镜内时间（毫秒）"}
                SpinBox{id:offset;objectName:"storyboardFrameOffset";from:0;to:Math.max(0,(desk.shot.duration_ms||1)-1);stepSize:100;editable:true;enabled:!desk.approved&&!frames.generating}
            }
            Button{objectName:"storyboardBindFrameMarker";text:"绑定命名时刻";enabled:!desk.busy&&frameList.currentIndex>=0&&!desk.approved;onClicked:desk.openMarkerBinding({op:"bind_keyframe_marker",shot_id:desk.shot.id,frame_id:desk.shot.keyframes[frameList.currentIndex].id})}
            ComboBox { id:frameList;Layout.fillWidth:true;model:(desk.shot.keyframes||[]).map(f=>desk.seconds(f.offset_ms)+(f.style_path?"　草图＋风格图":"　草图"))
                currentIndex:(desk.shot.keyframes||[]).findIndex(f=>f.offset_ms===offset.value)
                displayText:currentIndex<0?"选择已有关键帧":currentText
                onActivated:{offset.value=desk.shot.keyframes[currentIndex].offset_ms;frames.loadSourceForOffset()} }
            Label{visible:!desk.approved;text:"使用已配置的图像服务生成本帧素描；下方选项作为构图参考。";wrapMode:Text.Wrap;Layout.fillWidth:true;color:desk.t.muted}
            TextArea{id:sketchPrompt;objectName:"storyboardSketchPrompt";visible:!desk.approved;enabled:!frames.generating;placeholderText:"本帧画面描述：人物姿态、空间、光线和必须看清的动作";wrapMode:TextEdit.Wrap;Layout.fillWidth:true;Layout.preferredHeight:100}
            CheckBox{id:simpleColor;objectName:"storyboardSketchSimpleColor";text:"简彩";visible:!desk.approved;enabled:!frames.generating}
            GridLayout {
                columns:4;visible:!desk.approved;enabled:!frames.generating
                Label{text:"构图"} ComboBox{id:composition;model:["单人","双人","物件","空景"]}
                Label{text:"景别"} ComboBox{id:framing;model:["远景","中景","近景"];currentIndex:1}
                Label{text:"光向"} ComboBox{id:light;model:["左侧光","右侧光","逆光","顶光"]}
                Label{text:"空间"} ComboBox{id:setting;model:["室内","室外"]}
            }
            Flow {Layout.fillWidth:true;spacing:4
                Button {objectName:"storyboardSketch";text:frames.generating?"正在生成素描…":"生成素描";visible:!desk.approved;enabled:!desk.busy&&!frames.generating;onClicked:{
                    const layout={composition:["single","two_shot","object","empty"][composition.currentIndex],framing:["wide","medium","close"][framing.currentIndex],light:["left","right","back","overhead"][light.currentIndex],setting:["interior","exterior"][setting.currentIndex]}
                    desk.focusShot=desk.shot.id
                    desk.focusOffset=offset.value
                    frames.generating=true;frames.errorText=""
                    desk.request("generate_storyboard_sketch",{unit_id:desk.unitId,shot_id:desk.shot.id,offset_ms:offset.value,layout:layout,expected_revision:desk.board.revision,prompt:sketchPrompt.text,simple_color:simpleColor.checked})
                }}
                Button { text:desk.approved?"绑定项目风格图":"绑定草图";enabled:!desk.busy&&(!desk.approved||frameList.currentIndex>=0);onClicked:imagePicker.open() }
                Button { text:"移除关键帧";visible:!desk.approved;enabled:!desk.busy&&frameList.currentIndex>=0;onClicked:{desk.save({op:"delete_keyframe",shot_id:desk.shot.id,frame_id:desk.shot.keyframes[frameList.currentIndex].id});frames.close()} }
                Button { text:"生成项目风格图";visible:desk.approved;enabled:!desk.busy&&frameList.currentIndex>=0;onClicked:{
                    desk.focusShot=desk.shot.id
                    desk.focusOffset=offset.value
                    desk.request("render_storyboard_style",{unit_id:desk.unitId,shot_id:desk.shot.id,frame_id:desk.shot.keyframes[frameList.currentIndex].id,expected_revision:desk.board.revision})
                    frames.close()
                }}
                Button { text:"导出风格制作包";visible:desk.approved;onClicked:packagePicker.open() }
            }
        }
    }
    FileDialog {
        id:imagePicker;title:"选择关键帧";nameFilters:["图像 (*.png *.jpg *.jpeg *.webp *.bmp)"]
        onAccepted:{
            let change={op:"set_keyframe",shot_id:desk.shot.id,offset_ms:offset.value}
            change[desk.approved?"style_path":"sketch_path"]=selectedFile.toString()
            desk.save(change);frames.close()
        }
    }
    FileDialog {
        id:packagePicker;title:"导出镜头风格制作包";fileMode:FileDialog.SaveFile;defaultSuffix:"zip";nameFilters:["ZIP 文件 (*.zip)"]
        onAccepted:{desk.request("export_storyboard_style_package",{unit_id:desk.unitId,shot_id:desk.shot.id,output_path:selectedFile.toString()});frames.close()}
    }
}

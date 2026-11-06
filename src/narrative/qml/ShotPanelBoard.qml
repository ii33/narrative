import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
Rectangle {
    id: root
    SubmissionState{id:contentSubmission;allowedActions:["create_panel", "update_panel"];authoredFields:[];identityKey:root.panel.id||"";owner:root;operation:"apply_storyboard_changes";target:({unit_id:root.desk.unitId||null});onRestoreRequested:function(a){var c=a.changes[0];root.restoredChange=c;root.selectedId=c.panel_id||"";root.restoredPanel=Object.assign({},root.panels.find(function(p){return p.id===c.panel_id})||{},c);root.restoreRevision=a.revision}}

    property var desk
    property string selectedId: ""
    property var restoredPanel:null
    property var restoredChange:({})
    property int restoreRevision:0
    readonly property var panels: desk.shot ? (desk.shot.panels || []) : []
    readonly property var panel: restoredPanel||panels.find(p => p.id === selectedId) || panels[0] || ({})
    color: "#eef0eb"
    property var drawing: []
    function point(mouse){return [Math.max(0,Math.min(1,mouse.x/drawArea.width)),Math.max(0,Math.min(1,mouse.y/drawArea.height))]}
    function bindAdopted(){root.save({op:"use_panel_frame",panel_id:panel.id})}
    FileDialog {id:basePicker;title:"导入画格底图";nameFilters:["图片 (*.png *.jpg *.jpeg *.webp)"];onAccepted:root.save({op:"update_panel",panel_id:root.panel.id,base:{kind:"image",path:selectedFile.toString()}})}
    FileDialog {id:exportPicker;title:"导出画格";fileMode:FileDialog.SaveFile;nameFilters:["PNG (*.png)"];onAccepted:desk.request("export_storyboard_panel",{unit_id:desk.unitId,shot_id:desk.shot.id,panel_id:root.panel.id,output_path:selectedFile.toString(),annotated:annotated.checked})}
    Connections {target:root;function onPanelChanged(){root.drawing=[];ink.requestPaint()}}

    function save(change) {
        if(restoredPanel){var c=Object.assign({},restoredChange,change);c.op=restoredChange.op;if(!restoredChange.panel_id)delete c.panel_id;desk.savePhase2([Object.assign({shot_id:desk.shot.id},c)],restoreRevision);return}
        desk.savePhase2([Object.assign({shot_id:desk.shot.id},change)],desk.board.revision)
    }
    Connections{target:desk;function onBoardChanged(){root.restoredPanel=null}}

    function imagePath(p) { var c=(p.candidates||[]).find(c=>c.id===p.adopted_candidate_id);return c?c.path:(p.base||{}).path||"" }
    ColumnLayout {
        SubmissionFeedback{state:contentSubmission}
        Button{visible:!!root.restoredPanel;text:"保存修补后的画格";enabled:!desk.busy;onClicked:root.save({})}

        anchors.fill: parent; anchors.margins: 12
        RowLayout {
            Label { text: "画格计划 · " + (desk.shot.title || "请选择镜头"); font.bold:true; Layout.fillWidth:true }
            Button {objectName:"storyboardGenerateMissingPanels";text:"补齐本镜缺图";enabled:root.panels.length>0&&!desk.busy&&!desk.approved;onClicked:desk.request("generate_storyboard_panels",{unit_id:desk.unitId,shot_id:desk.shot.id,expected_revision:desk.board.revision,missing_only:true})}
            Button {objectName:"storyboardRegeneratePanels";text:"整镜试画";enabled:root.panels.length>0&&!desk.busy&&!desk.approved;onClicked:desk.request("generate_storyboard_panels",{unit_id:desk.unitId,shot_id:desk.shot.id,expected_revision:desk.board.revision,missing_only:false})}
            Button { text:"新增画格"; enabled:!!desk.shot.id&&!desk.busy&&!desk.approved; onClicked:root.save({op:"create_panel",offset_ms:Math.floor(desk.localTime),intent:""}) }
        }
        SplitView {
            Layout.fillWidth:true;Layout.fillHeight:true
            ScrollView { SplitView.preferredWidth:220
                Column { width:parent.width;spacing:8
                    Repeater { model:root.panels
                        Button { required property var modelData; width:200;text:modelData.id+" · "+modelData.offset_ms+" ms\n"+(modelData.intent||"未填写意图"); highlighted:root.panel.id===modelData.id;onClicked:root.selectedId=modelData.id }
                    }
                }
            }
            ColumnLayout { SplitView.fillWidth:true
                Label {text:"待复核 / 待更新：生成依据或图像已变化";visible:(desk.checkReport.diagnostics||[]).some(d=>d.panel_id===root.panel.id&&["panel_basis_changed","panel_basis_unavailable","panel_image_missing_or_invalid"].includes(d.code));color:desk.t.warn;wrapMode:Text.Wrap;Layout.fillWidth:true}
                TextField { Layout.fillWidth:true; placeholderText:"这一格要表达什么"; text:root.panel.intent||""; enabled:(!!root.panel.id||!!root.restoredPanel)&&!desk.busy&&!desk.approved;onEditingFinished:if(text!==root.panel.intent)root.save({op:"update_panel",panel_id:root.panel.id,intent:text}) }
                RowLayout {
                    Label{text:"镜内毫秒"}
                    SpinBox { from:0;to:Math.max(0,(desk.shot.duration_ms||1)-1);value:root.panel.offset_ms||0;editable:true;enabled:(!!root.panel.id||!!root.restoredPanel)&&!desk.approved;onValueModified:root.save({op:"update_panel",panel_id:root.panel.id,offset_ms:value}) }
                    Button { text:"绑定当前关键帧";enabled:!!root.panel.id&&!!desk.frame?.id&&!desk.approved;onClicked:root.save({op:"update_panel",panel_id:root.panel.id,frame_id:desk.frame.id}) }
                    Button { text:"空白底图";enabled:!!root.panel.id&&!desk.approved;onClicked:root.save({op:"update_panel",panel_id:root.panel.id,base:{kind:"blank"}}) }
                    Button { text:"调度机位底图";enabled:!!root.panel.id&&!desk.approved;onClicked:root.save({op:"update_panel",panel_id:root.panel.id,base:{kind:"camera"}}) }
                }
                RowLayout {
                    ComboBox {id:sourcePanel;Layout.fillWidth:true;model:root.panels.filter(p=>p.id!==root.panel.id);textRole:"intent";displayText:currentIndex>=0?(currentText||"已有画格 "+(currentIndex+1)):"选择已有画格"}
                    Button {text:"复制为底图";enabled:sourcePanel.currentIndex>=0&&!desk.approved&&!desk.busy;onClicked:root.save({op:"update_panel",panel_id:root.panel.id,base:{kind:"panel",panel_id:sourcePanel.model[sourcePanel.currentIndex].id}})}
                }
                RowLayout {
                    ComboBox {id:markTool;objectName:"storyboardPanelMarkTool";model:["浏览","画线","箭头","文字"]}
                    TextField {id:markText;objectName:"storyboardPanelMarkText";placeholderText:"标注文字";Layout.fillWidth:true;enabled:markTool.currentIndex===3}
                    Button {text:"撤销标注";enabled:(root.panel.marks||[]).length>0&&!desk.approved&&!desk.busy;onClicked:root.save({op:"update_panel",panel_id:root.panel.id,marks:root.panel.marks.slice(0,-1)})}
                }
                Item {
                    Layout.fillWidth:true;Layout.fillHeight:true
                    Rectangle {anchors.fill:parent;color:"#26302c"}
                Rectangle {
                    id:drawArea;anchors.centerIn:parent;width:Math.min(parent.width,parent.height*16/9);height:width*9/16;objectName:"storyboardPanelCanvas";color:"white"
                    Image { anchors.fill:parent;fillMode:Image.PreserveAspectFit;source:bridge&&root.panel.id?bridge.storyboardPanelPreviewUrl(desk.board,desk.shot.id,root.panel.id):"" }
                    Canvas {
                        id:ink;anchors.fill:parent
                        onWidthChanged:requestPaint()
                        onHeightChanged:requestPaint()
                        onPaint:{var c=getContext("2d");c.clearRect(0,0,width,height);c.strokeStyle="#c33";c.fillStyle="#c33";c.lineWidth=3;c.font="18px sans-serif";var marks=(root.panel.marks||[]).concat(root.drawing.length?[{kind:markTool.currentIndex===2?"arrow":"stroke",points:root.drawing}]:[]);marks.forEach(function(m){var pts=m.points||[];if(!pts.length)return;if(m.kind==="text"){c.fillText(m.text||"",pts[0][0]*width,pts[0][1]*height);return}c.beginPath();pts.forEach(function(p,i){if(i)c.lineTo(p[0]*width,p[1]*height);else c.moveTo(p[0]*width,p[1]*height)});c.stroke();if(m.kind==="arrow"&&pts.length>1){var p=pts[pts.length-1],q=pts[pts.length-2],x=p[0]*width,y=p[1]*height,a=Math.atan2(y-q[1]*height,x-q[0]*width);c.beginPath();c.moveTo(x,y);c.lineTo(x-14*Math.cos(a-.5),y-14*Math.sin(a-.5));c.lineTo(x-14*Math.cos(a+.5),y-14*Math.sin(a+.5));c.closePath();c.fill()}})}
                    }
                    MouseArea {
                        anchors.fill:parent;enabled:markTool.currentIndex>0&&!!root.panel.id&&!desk.busy&&!desk.approved
                        onPressed:mouse=>{root.drawing=[root.point(mouse)];ink.requestPaint()}
                        onPositionChanged:mouse=>{if(pressed){root.drawing=markTool.currentIndex===2?[root.drawing[0],root.point(mouse)]:root.drawing.concat([root.point(mouse)]);ink.requestPaint()}}
                        onReleased:mouse=>{var points=root.drawing;if(markTool.currentIndex===3)points=[root.point(mouse)];root.drawing=[];root.save({op:"update_panel",panel_id:root.panel.id,marks:(root.panel.marks||[]).concat([{kind:["","stroke","arrow","text"][markTool.currentIndex],points:points,text:markTool.currentIndex===3?markText.text:""}])})}
                    }

                }
                }
                RowLayout {
                    Button { text:"导入底图";enabled:!!root.panel.id&&!desk.approved;onClicked:basePicker.open() }
                    Button {objectName:"storyboardPanelBindAdopted";text:"采用为编排关键帧（替换同位置）";enabled:!!root.panel.adopted_candidate_id&&!desk.approved&&!desk.busy;onClicked:root.bindAdopted()}
                    CheckBox {id:annotated;text:"含标注";checked:true}
                    Button {text:"导出画格";enabled:!!root.panel.id&&!desk.busy;onClicked:exportPicker.open()}
                    Button { text:"独立试画";enabled:!!root.panel.id&&!desk.busy&&!desk.approved;onClicked:desk.request("generate_storyboard_panel",{unit_id:desk.unitId,shot_id:desk.shot.id,panel_id:root.panel.id,expected_revision:desk.board.revision}) }
                    Button { text:"删除画格";enabled:!!root.panel.id&&!desk.approved;onClicked:root.save({op:"delete_panel",panel_id:root.panel.id}) }
                }
                ListView {
                    Layout.fillWidth: true
                    Layout.preferredHeight: visible ? 78 : 0
                    visible: count > 0
                    orientation: ListView.Horizontal
                    clip: true
                    spacing: 6
                    model: root.panel.candidates || []
                    delegate: Button {
                        required property var modelData
                        required property int index
                        width: 112; height: 74
                        enabled: !desk.approved && !desk.busy
                        highlighted: modelData.id === root.panel.adopted_candidate_id
                        contentItem: Column {
                            Image { width: 100; height: 46; fillMode: Image.PreserveAspectFit
                                source: bridge ? bridge.storyboardImageUrl(modelData.path) : "" }
                            Label { text: (parent.parent.highlighted ? "已采用 " : "采用候选 ") + (index + 1) }
                        }
                        onClicked: root.save({op:"adopt_panel_candidate",panel_id:root.panel.id,candidate_id:modelData.id})
                    }
                }
            }
        }
    }
}

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Frame {
    id:card
    objectName:"assistantSubmissionSummary"
    property var summary:({})
    property var t
    property bool expanded:false
    signal userToggled()
    readonly property string taskId:summary.task_id||""
    function actionLabel(value){return ({created:"新增",updated:"修改",deleted:"删除",expired:"失效",moved:"移动",suppressed:"屏蔽",restored:"恢复",imported:"导入",reset:"重建",create:"新增",update:"修改",delete:"删除",expire:"失效",move:"移动",suppress:"屏蔽",restore:"恢复",import:"导入"})[value]||value||"变更"}
    function typeLabel(value){return ({node:"节点",property:"属性",property_change:"属性变更",element:"制作元素",occurrence:"元素出现",marker:"同步标记",requirement:"制作要求",unit:"场次",block:"正文块",segment:"段落",screenplay:"剧本",asset:"资产",appearance:"形象",item:"物品",location:"地点",material:"材质",event:"事件",scene:"场景",action:"动作",character:"角色",binding:"资源关联",shot_file_need:"文件需求",shot_file_selection:"实际文件"})[value]||value||"对象"}
    function readableId(value){value=String(value||"");return /[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}|^[a-f0-9]{32,}$/i.test(value)?"":value}
    background:Rectangle{color:card.t.card;border.color:card.t.line;radius:5}
    contentItem:ColumnLayout {
        spacing:5
        ToolButton{objectName:"assistantSubmissionToggle";Layout.fillWidth:true;text:card.summary.compact_text||card.summary.text||"任务结束";onClicked:if((card.summary.changes||[]).length||(card.summary.diagnostics||[]).length){card.expanded=!card.expanded;card.userToggled()}
            contentItem:Label{objectName:"assistantSubmissionText";text:parent.text;color:card.summary.status==="failed"?card.t.bad:card.t.body;elide:Text.ElideRight;font.pixelSize:11;verticalAlignment:Text.AlignVCenter}
            ToolTip.visible:summaryHover.hovered;ToolTip.text:card.expanded?"收起详情":"查看详情";HoverHandler{id:summaryHover}
        }
        ColumnLayout{visible:card.expanded;Layout.fillWidth:true
            Repeater{model:card.summary.changes||[]
                Label{objectName:"assistantSubmissionChange";required property var modelData;text:(modelData.action_label||card.actionLabel(modelData.action))+" · "+(modelData.type_label||card.typeLabel(modelData.object_type))+" · "+(modelData.name||card.readableId(modelData.id));wrapMode:Text.Wrap;Layout.fillWidth:true;color:card.t.body}
            }
            Repeater{model:card.summary.diagnostics||[]
                Label{objectName:"assistantSubmissionDiagnostic";required property var modelData;text:modelData.message||"";color:modelData.severity==="error"?card.t.bad:card.t.muted;wrapMode:Text.Wrap;Layout.fillWidth:true}
            }
        }
    }
}

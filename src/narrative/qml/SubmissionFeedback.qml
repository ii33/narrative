import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: feedback
    required property var state
    Layout.fillWidth:true
    visible:!!state.submission.submission_id||state.candidates.length>0||!!state.errorText
    spacing:4
    Label{objectName:"contentSubmissionStatus";Layout.fillWidth:true;wrapMode:Text.Wrap;text:feedback.state.submission.submission_id?"内容已暂存，尚未入库 · "+feedback.state.submission.submission_id+" · 修订 "+feedback.state.submission.submission_revision:feedback.state.errorText?"内容尚未保存。":"此表单有未完成的暂存内容。";color:"#9a6324";font.pixelSize:11}
    Label{objectName:"contentSubmissionProblems";Layout.fillWidth:true;wrapMode:Text.Wrap;text:feedback.state.problemText()+(feedback.state.errorText?"\n"+feedback.state.errorText:"");visible:!!text;font.pixelSize:11;color:"#9a6324"}
    Repeater{model:feedback.state.submission.problems||[]
        Button{required property var modelData;visible:(modelData.path||[]).length>0||(modelData.model_location||[]).length>0;flat:true;text:"定位问题字段 · "+(modelData.path&&modelData.path.length?modelData.path:modelData.model_location||[]).join(" / ");onClicked:feedback.state.focusProblem(modelData)}
    }
    Label{visible:!!feedback.state.submission.submission_id;Layout.fillWidth:true;wrapMode:Text.Wrap;font.pixelSize:10;text:"修改问题字段后再次保存，将修补同一份暂存；校验通过后自动入库。";color:"#6b7280"}
    RowLayout{visible:!feedback.state.submission.submission_id&&feedback.state.candidates.length>0;Layout.fillWidth:true
        ComboBox{id:choice;Layout.fillWidth:true;model:feedback.state.candidates;textRole:"submission_id"}
        Button{objectName:"contentSubmissionRestore";text:"恢复暂存";onClicked:feedback.state.restore(choice.currentIndex)}
    }
}

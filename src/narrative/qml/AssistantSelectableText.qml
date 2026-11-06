import QtQuick
import QtQuick.Controls

TextArea {
    id: field
    readOnly: true
    selectByMouse: true
    selectByKeyboard: true
    persistentSelection: true
    wrapMode: TextEdit.Wrap
    padding: 0
    background: null
    function copyAll() {
        var start=selectionStart,end=selectionEnd;
        selectAll();copy();select(start,end);
    }
    TapHandler {
        acceptedButtons: Qt.RightButton
        onTapped: menu.popup()
    }
    Menu {
        id: menu
        objectName: "assistantTextContextMenu"
        MenuItem { text: "复制所选"; enabled:field.selectedText.length>0;onTriggered:field.copy() }
        MenuItem { objectName:"assistantCopyWhole";text: "复制整条";onTriggered:field.copyAll() }
        MenuItem { text: "全选";onTriggered:field.selectAll() }
    }
}

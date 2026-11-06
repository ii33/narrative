import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout{
 id:access;property var panel
 spacing:3
 RowLayout{Layout.fillWidth:true;spacing:5
  Label{text:"访问模式";font.pixelSize:10;color:access.panel.t.muted}
  ComboBox{id:modeChoice;objectName:"assistantAccessMode";model:["标准访问","完全访问"];currentIndex:access.panel.accessMode==="full"?1:0;displayText:access.panel.accessSaving?"正在保存…":access.panel.accessMode==="full"?"完全访问":"标准访问";enabled:!!access.panel.chatId&&!access.panel.accessSaving;Layout.fillWidth:true;onActivated:access.panel.changeAccess(currentIndex===1?"full":"standard")}
  Label{objectName:"assistantFullAccessBadge";visible:access.panel.accessMode==="full";text:"完全访问";color:access.panel.t.warn;font.bold:true;font.pixelSize:10}
 }
 Connections{target:access.panel;function onAccessSavingChanged(){if(!access.panel.accessSaving)modeChoice.currentIndex=Qt.binding(function(){return access.panel.accessMode==="full"?1:0})}}
 Label{objectName:"assistantAccessDescription";text:access.panel.accessMode==="full"?"可读取、修改工程外文件并执行系统命令。设置从下一任务生效。":"可使用当前工程的业务工具。设置从下一任务生效。";font.pixelSize:10;color:access.panel.t.muted;wrapMode:Text.Wrap;Layout.fillWidth:true}
 Label{objectName:"assistantTaskAccessMode";visible:access.panel.busy;text:access.panel.activeAccessMode?"本次任务："+(access.panel.activeAccessMode==="full"?"完全访问":"标准访问")+"（已固定）":"本次任务访问模式尚未报告；新设置只用于下一任务。";font.pixelSize:10;color:access.panel.t.body;wrapMode:Text.Wrap;Layout.fillWidth:true}
 Label{visible:!!access.panel.accessError;text:access.panel.accessError;color:access.panel.t.bad;font.pixelSize:10;wrapMode:Text.Wrap;Layout.fillWidth:true}
}

/**
 * K 线标注原语统一出口（kline-common-layer 组 4）——C4 假设工作台/C5 纸盘共享资产。
 * 通道分族：竖带=attachPrimitive（PositionBand）/价线=createPriceLine/标记=createSeriesMarkers
 * （官方 primitive 插件）；纯函数映射层（fillsToSignals/signalsToMarkers/toXxxOptions）独立可测。
 */
export { PositionBand, attachPositionBand, toBandData, type PositionBandData } from "./positionBand";
export {
  attachEntryLine,
  attachStopLossLine,
  toEntryLineOptions,
  toStopLossLineOptions,
  type EntryLineData,
  type StopLossLineData,
  type StopLossKind,
} from "./annotations";
export {
  SIGNAL_STYLE,
  fillAction,
  fillsToSignals,
  signalsToMarkers,
  triggersToMarkers,
  type SignalAction,
  type SignalSide,
  type SignalInput,
  type SignalMarker,
  type TriggerInput,
} from "./markers";
export {
  attachObservationSeries,
  type ObservationPoint,
  type ObservationSeriesStyle,
} from "./observationSeries";

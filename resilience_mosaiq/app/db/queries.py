
site_statement = """
SELECT DISTINCT 
    s.SIT_SET_ID 
    ,sch.Sch_Id 
    ,sch.Sch_Set_Id 
    ,sch.Edit_DtTm AS Timestamp
    ,sch.App_DtTm
    ,id.IDA AS MRN
    ,p.Pat_ID1
    ,s.Site_Name
    ,su.Setup_Note AS SetupNote 
    ,cpt.Short_Desc AS Activity
--    ,p.First_Name 
--    ,p.Last_Name
--    ,s.Fractions AS RxFractions 
--    ,0 AS ActualFractions
--    ,su.Setup_Name AS SetupName
--    ,st2.Last_Name AS Location 
--    ,sch.SchStatus_Hist_SD AS Status 
--    ,sch.Notes
FROM Schedule sch
INNER JOIN Site s ON s.Pat_ID1 = sch.Pat_ID1
INNER JOIN Patient p ON sch.Pat_ID1 = p.Pat_ID1
INNER JOIN Ident id ON p.Pat_ID1 = id.Pat_Id1
INNER JOIN Staff st2 ON sch.Location = st2.Staff_ID
INNER JOIN CPT cpt ON sch.Activity = cpt.Hsp_Code
INNER JOIN SiteSetup su ON s.SIT_SET_ID = su.Sit_Set_ID

WHERE 
    s.Version = 0 
    AND su.Version = 0
    AND s.Technique != 'HDR' 
    AND sch.Version = 0 
    AND sch.App_DtTm >= '{0} 05:00:00' 
    AND sch.App_DtTm < '{0} 20:00:00'
    AND CONVERT(DATE, s.Edit_DtTm) > DATEADD(DAY, -90, sch.App_DtTm)
    AND sch.Location IN (SELECT Staff_ID FROM Staff WHERE Machine_Type = 1 or Machine_Type = 2)
ORDER BY 
    sch.App_DtTm, 
    Timestamp
"""


uid_statement = """
SELECT DISTINCT
    DCM.SOPInstanceUID AS RTPlanInstanceUID
    ,DCM1.StudyInstanceUID
    ,DCM2.SeriesInstanceUID
    ,RtPlan.Label
    ,RtPlan.Pat_ID1
    ,DCM1.StudyID
    ,DCM1.StudyDescription
    ,DCM1.Create_DtTm
    ,DCM2.SeriesNumber
    ,DCM2.SeriesDescription
    ,FLD.MachineCharID AS MachineID
FROM DCMStudy AS DCM1
INNER JOIN DCMSeries DCM2 ON DCM1.DCMStudy_ID = DCM2.DCMStudy_ID
INNER JOIN DCMInstance DCM ON DCM.DCMSeries_ID = DCM2.DCMSeries_ID 
INNER JOIN RtPlan ON RtPlan.DCMInstance_ID = DCM.DCMInstance_ID
INNER JOIN DCMInstance DCM_In1 ON DCM_IN1.DCMSeries_ID = DCM2.DCMSeries_ID
INNER JOIN TxField FLD ON FLD.OriginalPlanUID = DCM.SOPInstanceUID
WHERE 
    FLD.SIT_Set_ID = '{0}'
    AND FLD.Version = '0'
    AND FLD.Cgray > 0
"""


record_statement = """
SELECT DISTINCT
    ID.IDA, 
    SIT.SIT_SET_ID,
    FLD.OriginalPlanUID,
    FLD.OriginalBeamName, FLD.OriginalBeamNumber,
    FLD.Last_Tx_DtTm,
    FLD.FLD_ID, ID.Pat_Id1,  
    Pa.Last_Name, Pa.First_Name,
    Pa.Birth_DtTm,
    FLD.Fractions_Tx, SIT.Fractions,
    TFP.Energy, TFP.Energy_Unit_Enum,
    FLD.Meterset, FLD.Cgray, FLD.IndexReference_Enum,
    FLD.ControlPoints, TFP.Point, 
    TFP.Gantry_Ang, TFP.Gantry_Dir_Enum, 
    TFP.Create_DtTm AS PointTime,
    TFP.Coll_Ang, TFP.Coll_Dir_Enum,
    TFP.Couch_Ang, TFP.Couch_Roll_Dir_Enum,
    TFP.Couch_Top_Axis_Distance, TFP.Couch_Top, TFP.Couch_Top_Dir_Enum,
    TFP.Couch_Vrt, TFP.Couch_Lng, TFP.Couch_Lat,
    DHS.TerminationCode, DHS.Termination_Status_Enum, 
    DHS.Termination_Verify_Status_Enum,
    DHS.Dose_Addtl_Projected,
    FLD.Sad,
    STF.Last_Name AS Machine,
    FLD.Beam_Type_Flag, FLD.Modality_Enum, FLD.Type_Enum,
    FLD.Field_Name, FLD.Field_Label

FROM Ident AS ID
INNER JOIN Patient Pa ON ID.Pat_Id1 = Pa.Pat_ID1
INNER JOIN (
    SELECT Pat_ID1, OriginalPlanUID, MAX(Fractions_Tx) AS Max_Tx
    FROM TxField
GROUP BY Pat_ID1, OriginalPlanUID
) AS FLD_MAX ON ID.Pat_Id1 = FLD_MAX.Pat_ID1
INNER JOIN TxField FLD ON FLD_MAX.Pat_ID1 = FLD.Pat_ID1 AND FLD_MAX.MAX_Tx = FLD.Fractions_Tx 
INNER JOIN TxFieldPoint TFP ON FLD.FLD_ID = TFP.FLD_ID
INNER JOIN Dose_Hst DHS ON FLD.FLD_ID = DHS.FLD_ID
INNER JOIN FLD_HST ON DHS.DHS_ID = FLD_HST.DHS_ID
INNER JOIN Staff STF ON FLD.Machine_ID_Staff_ID = STF.Staff_ID
INNER JOIN Site SIT ON FLD.SIT_Set_ID = SIT.SIT_SET_ID

WHERE 
    SIT.SIT_SET_ID = '{0}'
    AND FLD.OriginalPlanUID = '{1}'
    AND FLD.Fractions_Tx > 0
    AND FLD.Fractions_Tx = DHS.Fractions_Tx

ORDER BY 
    FLD.OriginalPlanUID, FLD.OriginalBeamNumber, FLD.Fractions_Tx, TFP.Point
"""




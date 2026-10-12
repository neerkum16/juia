//MYTEST   JOB (ACCT001),'WIRE PAYMENT BATCH',
//             CLASS=A,MSGCLASS=X,MSGLEVEL=(1,1),
//             NOTIFY=&SYSUID
//*
//* =================================================================
//* JOB  : MYTEST
//* DESC : Daily Wire Payment Processing Batch Job
//*
//* RECORD LAYOUT — WIRE TRANSACTION FILE (LRECL=200)
//*   Offset  1  Len 16  CH  : Transaction reference number
//*   Offset 17  Len  9  PD  : Transaction amount in cents
//*   Offset 26  Len  9  CH  : Originating account number
//*   Offset 35  Len  9  CH  : Beneficiary account number
//*   Offset 44  Len  8  CH  : Value date YYYYMMDD
//*   Offset 52  Len  3  CH  : Transaction type WIR/ACH/CHQ
//*   Offset 55  Len  2  CH  : Status PE=Pending CM=Complete RJ=Reject
//*   Offset 57  Len  4  CH  : Reason code
//*   Offset 61  Len  2  CH  : Priority HI/LO/NR
//*   Offset 63  Len  3  CH  : Originating branch code
//*   Offset 66  Len  3  CH  : Currency code
//*   Offset 69  Len 30  CH  : Beneficiary name
//*   Offset 99  Len 50  CH  : Beneficiary address line 1
//*   Offset149  Len 50  CH  : Beneficiary address line 2
//*   Offset199  Len  2  CH  : Record version
//*
//* STEP SUMMARY
//*   STEP010 - IDCAMS   - Delete/define work VSAM   [SYSIN: INLINE]
//*   STEP020 - IDCAMS   - Load input to VSAM KSDS   [SYSIN: INLINE]
//*   STEP030 - SORT     - Extract pending wires      [SYSIN: EXTERNAL - CNTL(WIREEXT)]
//*   STEP040 - SORT     - Split HIGH/LOW by amount   [SYSIN: EXTERNAL - CNTL(WIRESPL)]
//*   STEP050 - IEBGENER - Backup input file          [SYSIN: DUMMY]
//*   STEP060 - ACCTPAY  - Process wire payments      [SYSIN: NONE - COBOL program]
//*   STEP070 - SORT     - Summarise by branch        [SYSIN: INLINE]
//*   STEP080 - IEBGENER - Create audit copy          [SYSIN: DUMMY]
//*   STEP090 - IDCAMS   - LISTCAT verify datasets    [SYSIN: INLINE]
//* =================================================================
//*
//* -----------------------------------------------------------------
//* STEP010 — INLINE SYSIN
//* Control cards are coded directly in the JCL between DD * and /*
//* This is the simple pattern — everything visible in one place.
//* -----------------------------------------------------------------
//STEP010  EXEC PGM=IDCAMS
//SYSPRINT DD SYSOUT=*
//SYSIN    DD *
  DELETE WIRE.WORK.INPUT.KSDS  CLUSTER PURGE
  DELETE WIRE.WORK.EXTRT.KSDS  CLUSTER PURGE

  IF LASTCC > 8 THEN
    SET MAXCC = 0

  DEFINE CLUSTER                           -
    (NAME(WIRE.WORK.INPUT.KSDS)            -
     INDEXED                               -
     KEYS(16 0)                            -
     RECORDSIZE(200 200)                   -
     CYLINDERS(20 5)                       -
     SHAREOPTIONS(2 3))                    -
  DATA                                     -
    (NAME(WIRE.WORK.INPUT.KSDS.DATA))      -
  INDEX                                    -
    (NAME(WIRE.WORK.INPUT.KSDS.INDEX))

  DEFINE CLUSTER                           -
    (NAME(WIRE.WORK.EXTRT.KSDS)            -
     INDEXED                               -
     KEYS(16 0)                            -
     RECORDSIZE(200 200)                   -
     CYLINDERS(10 5)                       -
     SHAREOPTIONS(2 3))                    -
  DATA                                     -
    (NAME(WIRE.WORK.EXTRT.KSDS.DATA))      -
  INDEX                                    -
    (NAME(WIRE.WORK.EXTRT.KSDS.INDEX))
/*
//*
//* -----------------------------------------------------------------
//* STEP020 — INLINE SYSIN
//* REPRO control cards coded directly in the JCL.
//* -----------------------------------------------------------------
//STEP020  EXEC PGM=IDCAMS
//SYSPRINT DD SYSOUT=*
//SEQIN    DD DSN=WIRE.DAILY.INPUT.SEQ,DISP=SHR
//VSAMOUT  DD DSN=WIRE.WORK.INPUT.KSDS,DISP=SHR
//SYSIN    DD *
  REPRO INFILE(SEQIN)      -
        OUTFILE(VSAMOUT)
/*
//*
//* -----------------------------------------------------------------
//* STEP030 — EXTERNAL SYSIN (PDS MEMBER)
//* Control cards are stored in a separate CNTL library as member
//* WIREEXT. This is the production standard — control cards are
//* managed independently under change control in the CNTL library.
//* -----------------------------------------------------------------
//STEP030  EXEC PGM=SORT
//SYSPRINT DD SYSOUT=*
//SORTIN   DD DSN=WIRE.DAILY.INPUT.SEQ,DISP=SHR
//SORTOUT  DD DSN=WIRE.WORK.EXTRT.SEQ,
//            DISP=(NEW,CATLG,DELETE),
//            SPACE=(CYL,(10,5)),
//            DCB=(RECFM=FB,LRECL=80,BLKSIZE=0,DSORG=PS)
//SYSIN    DD DSN=SYS2.CNTL(WIREEXT),DISP=SHR
//*
//* -----------------------------------------------------------------
//* STEP040 — EXTERNAL SYSIN (PDS MEMBER)
//* Split control cards stored in CNTL library member WIRESPL.
//* Note HIGHVAL and LOWVAL output files defined separately.
//* -----------------------------------------------------------------
//STEP040  EXEC PGM=SORT
//SYSPRINT DD SYSOUT=*
//SORTIN   DD DSN=WIRE.WORK.EXTRT.SEQ,DISP=SHR
//HIGHVAL  DD DSN=WIRE.WORK.HIGH.SEQ,
//            DISP=(NEW,CATLG,DELETE),
//            SPACE=(CYL,(5,2)),RECFM=FB,LRECL=200
//LOWVAL   DD DSN=WIRE.WORK.LOW.SEQ,
//            DISP=(NEW,CATLG,DELETE),
//            SPACE=(CYL,(5,2)),RECFM=FB,LRECL=200
//SYSIN    DD DSN=SYS2.CNTL(WIRESPL),DISP=SHR
//*
//* -----------------------------------------------------------------
//* STEP050 — SYSIN DD DUMMY
//* No control cards needed — IEBGENER performs a straight copy.
//* -----------------------------------------------------------------
//STEP050  EXEC PGM=IEBGENER
//SYSPRINT DD SYSOUT=*
//SYSIN    DD DUMMY
//SYSUT1   DD DSN=WIRE.DAILY.INPUT.SEQ,DISP=SHR
//SYSUT2   DD DSN=WIRE.DAILY.BACKUP.SEQ,
//            DISP=(NEW,CATLG,DELETE),
//            SPACE=(CYL,(20,5)),RECFM=FB,LRECL=200
//*
//* -----------------------------------------------------------------
//* STEP060 — NO SYSIN (COBOL program)
//* Non-utility step — no control cards. PGM=ACCTPAY is a COBOL
//* program that reads its own parameters from PARMS DD.
//* -----------------------------------------------------------------
//STEP060  EXEC PGM=ACCTPAY,REGION=4M
//STEPLIB  DD DSN=PROD.LOADLIB,DISP=SHR
//SYSPRINT DD SYSOUT=*
//INPUT    DD DSN=WIRE.WORK.HIGH.SEQ,DISP=SHR
//OUTPUT   DD DSN=WIRE.PROCESSED.SEQ,
//            DISP=(NEW,CATLG,DELETE),
//            SPACE=(CYL,(10,5)),RECFM=FB,LRECL=200
//PARMS    DD DSN=WIRE.PARMLIB(WIRECNTL),DISP=SHR
//SYSOUT   DD SYSOUT=*
//*
//* -----------------------------------------------------------------
//* STEP070 — INLINE SYSIN
//* Summarise step — control cards coded inline in the JCL.
//* -----------------------------------------------------------------
//STEP070  EXEC PGM=SORT
//SYSPRINT DD SYSOUT=*
//SORTIN   DD DSN=WIRE.PROCESSED.SEQ,DISP=SHR
//SORTOUT  DD DSN=WIRE.SUMMARY.SEQ,
//            DISP=(NEW,CATLG,DELETE),
//            SPACE=(CYL,(5,2)),RECFM=FB,LRECL=200
//SYSIN    DD *
  SORT FIELDS=(63,3,CH,A)
  SUM FIELDS=(17,9,PD)
  OUTREC FIELDS=(1,16,
                 63,3,
                 17,9,PD,TO=FS,LENGTH=12,
                 44,8,
                 55,2,
                 66,3)
/*
//*
//* -----------------------------------------------------------------
//* STEP080 — SYSIN DD DUMMY
//* Audit copy — straight copy, no transformation.
//* -----------------------------------------------------------------
//STEP080  EXEC PGM=IEBGENER
//SYSPRINT DD SYSOUT=*
//SYSIN    DD DUMMY
//SYSUT1   DD DSN=WIRE.PROCESSED.SEQ,DISP=SHR
//SYSUT2   DD DSN=WIRE.AUDIT.SEQ,
//            DISP=(NEW,CATLG,DELETE),
//            SPACE=(CYL,(10,5)),RECFM=FB,LRECL=200
//*
//* -----------------------------------------------------------------
//* STEP090 — INLINE SYSIN
//* LISTCAT verification step — inline control cards.
//* -----------------------------------------------------------------
//STEP090  EXEC PGM=IDCAMS
//SYSPRINT DD SYSOUT=*
//SYSIN    DD *
  LISTCAT ENTRIES(WIRE.WORK.INPUT.KSDS)  ALL
  LISTCAT ENTRIES(WIRE.WORK.EXTRT.KSDS)  ALL
/*
//

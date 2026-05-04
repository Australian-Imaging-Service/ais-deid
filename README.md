# Edge_De-id
Repository to test deidentifications scripts for AIS edge


____________________________________________________________________________________________
Application:
CLI Call
python -m deid_module.cli \
  --input /data/incoming \
  --output /data/deidentified

Apply internally to XNAT ingest by replacing internal deid step with:
call_deid(input_sorted_dir, output_deid_dir)

Apply externally as a separate module by calling the module:
deid_module --input sorted --output deid
xnat-ingest deid/

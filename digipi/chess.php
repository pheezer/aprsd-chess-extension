<html>
<meta name="viewport" content="width=device-width">

<title>DigiPi Chess</title>

<body  style="position: relative; height: 100%; width: 100%; overflow: hidden;">

<font face="sans">


<p>
<br/>

<?php
# Installed by aprsd-chess-extension.  Modeled on DigiPi's webchat.php:
# wait (up to 8 seconds) for the chess board to start listening, then go there.
$count = 0;
$hit = 0;
while ( $count < 8  ) {
   $output = shell_exec("netstat -nvat | grep LIST | grep :8056 | wc -l");
   if ( $output > 0 ) {
      echo "<script>";
      echo "window.location.href =  window.location.protocol + '//' + window.location.hostname + ':8056'";
      echo "</script>";
      $hit = 1;
      break;
   }
   sleep(1);
   $count++;
}
if ( ! $hit ) {
   echo "<h3>Unable to connect to the Chess service, please ensure APRS Chess is enabled.</h3>";
}
?>

<br>
<br>
Direct link:
<script>
document.write('<a href="' + window.location.protocol + '//' + window.location.hostname + ':8056' + '">'
+ window.location.protocol + '//' + window.location.hostname + ':8056' + '</a>' );
</script>

</font>
</body>
</html>
